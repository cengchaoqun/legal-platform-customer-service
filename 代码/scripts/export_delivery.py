"""导出可审查的源码和验收截图；拒绝复制密钥、依赖、旧模型材料或历史仓库。"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil

CODE = Path(__file__).resolve().parents[1]
EXCLUDED = {".git", ".venv", "node_modules", "dist", "__pycache__", ".pytest_cache",
            ".workbuddy", ".idea", ".vscode", ".runtime", "training", "evaluation", "docs",
            "mock_ai"}


def ignore(directory, names):
    return [n for n in names if n in EXCLUDED or n.endswith((".pyc", ".log"))
            or (n.startswith(".env") and n != ".env.example") or n == "uv.lock"
            or n == "cdp_shot.py"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=CODE.parent/"交付成果/legal-platform-customer-service")
    dest = parser.parse_args().out.resolve()
    if dest.exists() and any(dest.iterdir()):
        raise SystemExit("交付目录非空，拒绝覆盖；请指定新的 --out 目录。")
    dest.mkdir(parents=True, exist_ok=True)
    for name in ("ai-service", "customer-service", "legal-business-service", "user-frontend", "admin-frontend", "deploy", "tests", "scripts"):
        shutil.copytree(CODE/name, dest/"代码"/name, ignore=ignore)
    for name in ("requirements.txt", "pytest.ini", "交付说明.md"):
        shutil.copy2(CODE/name, dest/"代码"/name)
    shutil.copytree(CODE/"reports", dest/"代码/reports", ignore=lambda d, ns: [n for n in ns if n=="screenshots"])
    shutil.copytree(CODE/"reports/screenshots", dest/"测试截图")
    readme = (CODE/"交付说明.md").read_text(encoding="utf-8")
    readme += "\n## 运行截图\n\n![真实 DeepSeek 客服对话](测试截图/01-DeepSeek客服对话.png)\n\n![DeepSeek 运行审计](测试截图/07-DeepSeek运行审计.png)\n"
    (dest/"README.md").write_text(readme, encoding="utf-8")
    (dest/".gitignore").write_text(".venv/\nnode_modules/\ndist/\n__pycache__/\n.pytest_cache/\n.env\n.env.*\n!.env.example\n*.log\n.runtime/\n.local-backup/\n", encoding="utf-8")
    files = sorted(p for p in dest.rglob("*") if p.is_file())
    secret = os.environ.get("DEEPSEEK_API_KEY", "")
    for p in files:
        data = p.read_bytes()
        if (secret and secret.encode() in data) or re.search(rb"sk-[A-Za-z0-9]{24,}", data):
            raise SystemExit("发现疑似密钥，禁止发布："+str(p.relative_to(dest)))
        if any(part in EXCLUDED for part in p.relative_to(dest).parts):
            raise SystemExit("发现排除项："+str(p.relative_to(dest)))
    manifest = [{"path":p.relative_to(dest).as_posix(), "bytes":p.stat().st_size,
                 "sha256":hashlib.sha256(p.read_bytes()).hexdigest()} for p in files]
    (dest/"交付清单.json").write_text(json.dumps(dict(files=manifest, secret_scan="passed", dependency_directories="excluded"), ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(dict(directory=str(dest), files=len(files)+1, screenshots=len(list((dest/"测试截图").glob("*.png"))), secret_scan="passed"), ensure_ascii=False))


if __name__ == "__main__":
    main()
