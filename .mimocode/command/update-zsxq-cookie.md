---
description: "更新知识星球 Cookie：写入 cookies.json、设置 GitHub Secret、验证、提交"
---

# 更新 ZSXQ Cookie

用户粘贴新的知识星球 Cookie JSON 后，执行以下步骤：

## 输入

`$ARGUMENTS` — 用户粘贴的 Cookie JSON 内容（EditThisCookie 导出格式）

## 步骤

1. **写入 cookies.json**
   - 将用户粘贴的 JSON 内容写入项目根目录 `cookies.json`
   - 确保包含 `zsxq_access_token` 字段

2. **语法验证**
   ```bash
   python -m py_compile main.py crawler.py auth.py
   ```

3. **安全检查**
   ```bash
   grep -rn "zsxq_access_token\|password\|secret\|api_key\|token" --include="*.py" . | grep -v ".git/" | grep -v "__pycache__"
   ```
   确认无硬编码密钥泄露。

4. **更新 GitHub Secret**（如用户需要 CI 同步）
   ```bash
   gh secret set ZSXQ_COOKIES < cookies.json
   ```

5. **提交**
   ```bash
   git add cookies.json
   git commit -m "update zsxq cookies"
   git push
   ```

## 注意

- `cookies.json` 在 `.gitignore` 中，不会被 git 跟踪，这是正确的安全实践
- Cookie 过期时间以 API 返回的 401/403 为准，浏览器导出的 `expires` 字段可能不准确
- CI 环境无法自动刷新 Cookie，需要本地扫码登录后手动更新 Secret
