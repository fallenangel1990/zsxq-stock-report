"""知识星球附件解析模块。

将 PDF / 音频附件转换为可供总结和股票提取使用的文本。
解析失败不阻断主流程，避免单个附件拖垮日报。
"""

from __future__ import annotations

import os
import re
from io import BytesIO
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import requests
import yaml


PDF_EXTENSIONS = {".pdf"}
AUDIO_EXTENSIONS = {".mp3", ".m4a", ".wav", ".aac", ".ogg", ".webm", ".mpga", ".mpeg"}


def _log(msg: str) -> None:
    print(msg, flush=True)


def _load_config() -> dict:
    config_path = Path(__file__).parent / "config.yaml"
    if config_path.exists():
        with open(config_path, "r") as f:
            return yaml.safe_load(f) or {}
    return {}


def _attachment_config() -> dict:
    config = _load_config().get("attachments", {}) or {}
    return {
        "enabled": config.get("enabled", True),
        "parse_pdf": config.get("parse_pdf", True),
        "transcribe_audio": config.get("transcribe_audio", True),
        "max_file_mb": config.get("max_file_mb", 25),
        "max_pdf_pages": config.get("max_pdf_pages", 40),
        "max_text_chars": config.get("max_text_chars", 18000),
        "audio_model": config.get("audio_model", "whisper-1"),
    }


def enrich_posts_with_attachments(
    posts: list[dict],
    headers: Optional[dict] = None,
) -> list[dict]:
    """下载并解析帖子中的附件，将文本写入 attachment_text。

    Args:
        posts: crawler.py 解析后的帖子列表。
        headers: 下载私有附件时使用的请求头。

    Returns:
        原帖子列表（就地补充 attachment_text / attachment_notes）。
    """
    config = _attachment_config()
    if not config.get("enabled"):
        return posts

    total_files = sum(len(p.get("files", []) or []) for p in posts)
    if not total_files:
        return posts

    _log(f"[附件解析] 检测到 {total_files} 个附件，开始解析 PDF/音频文本")
    for post in posts:
        texts = []
        notes = []
        for file_info in post.get("files", []) or []:
            result = _parse_attachment(file_info, headers=headers, config=config)
            if result.get("text"):
                texts.append(
                    f"【附件：{result.get('name') or '未命名'}】\n{result['text']}"
                )
            if result.get("note"):
                notes.append(result["note"])

        if texts:
            post["attachment_text"] = "\n\n".join(texts)
        if notes:
            post["attachment_notes"] = notes

    return posts


def _parse_attachment(file_info: dict, headers: Optional[dict], config: dict) -> dict:
    name = file_info.get("name") or _filename_from_url(file_info.get("url", "")) or "未命名附件"
    url = file_info.get("url", "")
    ext = _attachment_extension(file_info)
    if not url:
        return {"name": name, "note": f"{name}: 缺少下载地址"}

    if ext in PDF_EXTENSIONS and config.get("parse_pdf"):
        return _parse_pdf_attachment(name, url, headers=headers, config=config)

    if ext in AUDIO_EXTENSIONS and config.get("transcribe_audio"):
        return _parse_audio_attachment(name, url, headers=headers, config=config)

    return {"name": name, "note": f"{name}: 非 PDF/音频附件，已跳过"}


def _parse_pdf_attachment(
    name: str,
    url: str,
    headers: Optional[dict],
    config: dict,
) -> dict:
    data = _download_attachment(url, headers=headers, max_file_mb=config["max_file_mb"])
    if not data:
        return {"name": name, "note": f"{name}: PDF 下载失败或超过大小限制"}

    try:
        from pypdf import PdfReader
    except ImportError:
        return {"name": name, "note": f"{name}: 缺少 pypdf 依赖，无法解析 PDF"}

    try:
        reader = PdfReader(BytesIO(data))
        page_limit = min(len(reader.pages), int(config["max_pdf_pages"]))
        page_texts = []
        for page in reader.pages[:page_limit]:
            text = page.extract_text() or ""
            if text.strip():
                page_texts.append(text)
        text = _normalize_text("\n".join(page_texts))[: int(config["max_text_chars"])]
        if not text:
            return {"name": name, "note": f"{name}: PDF 未提取到文本，可能是扫描件"}
        _log(f"[附件解析] PDF {name}: 提取 {len(text)} 字")
        return {"name": name, "text": text}
    except Exception as exc:
        return {"name": name, "note": f"{name}: PDF 解析失败 {type(exc).__name__}"}


def _parse_audio_attachment(
    name: str,
    url: str,
    headers: Optional[dict],
    config: dict,
) -> dict:
    api_key = (
        os.environ.get("AUDIO_TRANSCRIPTION_API_KEY", "").strip()
        or os.environ.get("OPENAI_API_KEY", "").strip()
    )
    if not api_key:
        return {"name": name, "note": f"{name}: 缺少 OPENAI_API_KEY/AUDIO_TRANSCRIPTION_API_KEY，跳过音频转写"}

    data = _download_attachment(url, headers=headers, max_file_mb=config["max_file_mb"])
    if not data:
        return {"name": name, "note": f"{name}: 音频下载失败或超过大小限制"}

    try:
        from openai import OpenAI

        audio_file = BytesIO(data)
        audio_file.name = name if _attachment_extension({"name": name}) else f"{name}.mp3"
        client = OpenAI(api_key=api_key)
        response = client.audio.transcriptions.create(
            model=config.get("audio_model", "whisper-1"),
            file=audio_file,
        )
        text = _normalize_text(getattr(response, "text", "") or str(response))
        text = text[: int(config["max_text_chars"])]
        if not text:
            return {"name": name, "note": f"{name}: 音频转写结果为空"}
        _log(f"[附件解析] 音频 {name}: 转写 {len(text)} 字")
        return {"name": name, "text": text}
    except Exception as exc:
        return {"name": name, "note": f"{name}: 音频转写失败 {type(exc).__name__}"}


def _download_attachment(
    url: str,
    headers: Optional[dict],
    max_file_mb: int,
) -> bytes:
    max_bytes = max(1, int(max_file_mb)) * 1024 * 1024
    try:
        resp = requests.get(url, headers=headers or {}, timeout=45, stream=True)
        if resp.status_code != 200:
            return b""
        content_length = int(resp.headers.get("Content-Length") or 0)
        if content_length and content_length > max_bytes:
            return b""

        chunks = []
        total = 0
        for chunk in resp.iter_content(chunk_size=1024 * 256):
            if not chunk:
                continue
            total += len(chunk)
            if total > max_bytes:
                return b""
            chunks.append(chunk)
        return b"".join(chunks)
    except requests.RequestException:
        return b""


def _attachment_extension(file_info: dict) -> str:
    name = file_info.get("name", "")
    url = file_info.get("url", "")
    mime = (file_info.get("mime_type") or file_info.get("content_type") or "").lower()

    for source in (name, urlparse(url).path):
        suffix = Path(source).suffix.lower()
        if suffix:
            return suffix

    if "pdf" in mime:
        return ".pdf"
    if "mpeg" in mime or "mp3" in mime:
        return ".mp3"
    if "audio" in mime:
        return ".mp3"
    return ""


def _filename_from_url(url: str) -> str:
    path = urlparse(url or "").path
    return Path(path).name


def _normalize_text(text: str) -> str:
    text = re.sub(r"[ \t]+", " ", text or "")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
