# 发布说明

本仓库是 Home Assistant 自定义集成 **Staggered Switch**，可直接通过 HACS
以「自定义存储库」方式安装。

## 仓库信息

- 仓库名：`staggered-switch`
- 可见性：Public（HACS 只能从公开仓库安装）
- 结构：`custom_components/staggered_switch/` 为集成本体，`brand/` 为图标

## 发布新版本

1. 修改 `custom_components/staggered_switch/manifest.json` 中的 `version`
2. 提交并推送：

```bash
git add .
git commit -m "描述这次改动"
git push origin main
```

3. 打标签并推送，HACS 才会看到新版本：

```bash
git tag v1.2.0
git push origin v1.2.0
```

4. 在 GitHub 上为该标签创建 Release（可选，但推荐填写变更说明）

## 提交前的自检

```bash
python check_layout.py    # 检查 HACS 目录结构与 manifest
python -m tests.selfcheck # 纯逻辑测试，不需要 Home Assistant
python -m tests.verify    # 端到端测试，会启动真实 HA 核心
```

三项都通过后再推送。

## 在 HACS 中安装

1. HACS → 右上角菜单 → 自定义存储库
2. 添加仓库地址，类别选 **Integration**
3. 搜索 Staggered Switch 并安装
4. 重启 Home Assistant

## 仓库中刻意不包含的内容

- 任何服务器地址、访问令牌、实体 id、个人排班表
- 本机绝对路径、个人邮箱
- 仅本地使用的辅助脚本（已在 `.gitignore` 中排除）
