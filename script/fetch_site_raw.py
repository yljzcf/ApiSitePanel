from datetime import datetime, timezone
from pathlib import Path

import requests

from site_api_utils import normalize_base_url, save_json, site_dir_name_from_url


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
SITE_ROOT_DIR = PROJECT_ROOT / "site"
ENDPOINTS = {
    "pricing": ["/api/pricing", "/v1/models", "/models/public"],
    "user_groups": ["/api/user/groups", "/api/v1/auth/me", "/upstreams/public"],
}


def fetch_json(session, base_url, path, allow_statuses=None):
    url = f"{base_url}{path}"
    response = session.get(url, timeout=20)
    if allow_statuses and response.status_code in allow_statuses:
        return response.json(), response.status_code
    response.raise_for_status()
    return response.json(), response.status_code


def _build_session():
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "Mozilla/5.0 Fetch-Tool",
            "Accept": "application/json",
        }
    )
    return session


def _fetch_one_site(session, base_url, site_name, site_root_dir=SITE_ROOT_DIR):
    raw_dir = Path(site_root_dir) / site_name / "raw"
    meta = {
        "site_name": site_name,
        "base_url": base_url,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "endpoints": {},
    }

    for file_stem, paths in ENDPOINTS.items():
        last_error = None
        for path in paths:
            print(f"[*] 正在抓取 {path} ...")
            try:
                payload, status_code = fetch_json(session, base_url, path, allow_statuses={401})
                if status_code == 401:
                    meta["endpoints"][file_stem] = {
                        "path": path,
                        "http_status": status_code,
                        "ok": False,
                        "auth_failed": True,
                        "error": "鉴权失败或 token 已失效",
                    }
                    print(f"[-] 抓取 {path} 失败: 鉴权失败或 token 已失效")
                    break
                save_json(raw_dir / f"{file_stem}.json", payload)
                meta["endpoints"][file_stem] = {
                    "path": path,
                    "http_status": status_code,
                    "ok": True,
                }
                print(f"[+] 已保存 {file_stem}.json")
                break
            except requests.exceptions.HTTPError as exc:
                last_error = exc
                response = getattr(exc, "response", None)
                if response is None or response.status_code != 404:
                    meta["endpoints"][file_stem] = {
                        "path": path,
                        "ok": False,
                        "error": str(exc),
                    }
                    print(f"[-] 抓取 {path} 失败: {exc}")
                    break
            except Exception as exc:
                last_error = exc
                meta["endpoints"][file_stem] = {
                    "path": path,
                    "ok": False,
                    "error": str(exc),
                }
                print(f"[-] 抓取 {path} 失败: {exc}")
                break
        else:
            meta["endpoints"][file_stem] = {
                "path": paths[-1],
                "ok": False,
                "error": str(last_error) if last_error else "未知错误",
            }
            print(f"[-] 抓取 {paths[-1]} 失败: {last_error}")

    save_json(raw_dir / "meta.json", meta)
    print(f"[+] 抓取结束，输出目录：{raw_dir}")
    return raw_dir, meta


def fetch_one_site(session, base_url, site_name, site_root_dir=SITE_ROOT_DIR):
    normalized_base_url = normalize_base_url(base_url)
    normalized_site_name = site_dir_name_from_url(normalized_base_url, site_name)
    return _fetch_one_site(session, normalized_base_url, normalized_site_name, site_root_dir)


def main():
    session = _build_session()
    print("[*] 请输入站点 URL；输入 q 或直接留空可退出。")

    while True:
        raw_input_url = input("请输入中转站的网址 URL (例如: https://api.example.com): ").strip()
        if not raw_input_url or raw_input_url.lower() == "q":
            print("[+] 已结束抓取。")
            return 0
        try:
            base_url = normalize_base_url(raw_input_url)
        except ValueError as exc:
            print(f"[-] {exc}")
            continue

        raw_input_site_name = input("请输入站点名 (用于建立文件夹和后续展示): ").strip()
        if not raw_input_site_name:
            print("[-] 站点名不能为空")
            continue

        site_name = site_dir_name_from_url(base_url, raw_input_site_name)
        _fetch_one_site(session, base_url, site_name, SITE_ROOT_DIR)


if __name__ == "__main__":
    raise SystemExit(main())
