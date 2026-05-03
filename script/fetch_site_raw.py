from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import requests

from site_api_utils import (
    build_site_records,
    build_topup_plan_records,
    normalize_base_url,
    public_upstreams_have_available_models,
    save_json,
    site_dir_name_from_url,
)


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
SITE_ROOT_DIR = PROJECT_ROOT / "site"
ENDPOINTS = {
    "pricing": ["/api/pricing", "/v1/models", "/models/public"],
    "user_groups": ["/api/user/groups", "/api/v1/groups/available", "/api/v1/auth/me", "/upstreams/public"],
    "topup": [
        "/api/user/topup/info",
        "/api/topup",
        "/api/topup/options",
        "/api/user/topup",
        "/api/user/topup/options",
        "/api/recharge",
        "/api/recharge/options",
        "/api/payment/topup",
        "/api/pay/topup",
    ],
    "plans": [
        "/api/subscription/plans",
        "/api/plans",
        "/api/plan/enabled?purchasable=true",
        "/api/user/plans",
        "/api/products",
        "/api/packages",
        "/api/shop/plans",
        "/api/pay/plans",
    ],
}


def build_ccplus_channels_endpoint(base_url, authorization):
    parsed = urlparse(normalize_base_url(base_url))
    if parsed.netloc.lower() != "hk.ccplus.site":
        raise ValueError("CCplus 专用 channels 接口仅适用于 hk.ccplus.site")
    token = str(authorization or "").strip()
    if token.startswith("Bearer "):
        token = token[len("Bearer ") :].strip()
    if not token:
        raise ValueError("CCplus channels 接口需要 Authorization token")
    return "https://pay.ccplus.site", "/api/channels?" + urlencode({"token": token})


def redact_endpoint_path(path):
    parsed = urlparse(path)
    query = urlencode([(key, "<redacted>" if key.lower() == "token" else value) for key, value in parse_qsl(parsed.query)])
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, query, parsed.fragment))


def fetch_json(session, base_url, path, allow_statuses=None):
    url = f"{base_url}{path}"
    response = session.get(url, timeout=20)
    if allow_statuses and response.status_code in allow_statuses:
        try:
            return response.json(), response.status_code
        except ValueError:
            return None, response.status_code
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


def apply_new_api_user_auth(session, new_api_user):
    token = str(new_api_user or "").strip()
    if not token:
        return False
    session.headers["New-Api-User"] = token
    return True


def apply_token_auth(session, token):
    normalized_token = str(token or "").strip()
    if not normalized_token:
        return False
    session.headers["Authorization"] = f"Bearer {normalized_token}"
    return True


def apply_authorization_auth(session, authorization):
    normalized_authorization = str(authorization or "").strip()
    if not normalized_authorization:
        return False
    session.headers["Authorization"] = normalized_authorization
    return True


def apply_auth(session, new_api_user=None, token=None, authorization=None):
    return {
        "new_api_user_configured": apply_new_api_user_auth(session, new_api_user),
        "token_configured": apply_token_auth(session, token),
        "authorization_configured": apply_authorization_auth(session, authorization),
        "authorization_source": "Authorization" if str(authorization or "").strip() else ("token" if str(token or "").strip() else None),
    }


def _pricing_paths_for_site(base_url, site_name, paths):
    parsed = urlparse(normalize_base_url(base_url))
    normalized_site_name = str(site_name or "").strip().lower()
    if parsed.netloc.lower() != "www.findcg.com" and normalized_site_name != "发现":
        return paths
    ordered_paths = []
    for path in paths:
        if path == "/v1/models" and "/api/v1/model-square" not in ordered_paths:
            ordered_paths.append("/api/v1/model-square")
        if path not in ordered_paths:
            ordered_paths.append(path)
    if "/api/v1/model-square" not in ordered_paths:
        ordered_paths.append("/api/v1/model-square")
    return ordered_paths


def _payload_shape(payload):
    if isinstance(payload, dict):
        shape = {"type": "dict", "keys": sorted(str(key) for key in payload.keys())[:20]}
        for key, value in payload.items():
            if isinstance(value, list):
                shape[f"{key}_length"] = len(value)
            elif isinstance(value, dict):
                shape[f"{key}_keys"] = sorted(str(nested_key) for nested_key in value.keys())[:20]
        return shape
    if isinstance(payload, list):
        return {"type": "list", "length": len(payload)}
    return {"type": type(payload).__name__}


def is_payload_meaningful(file_stem, payload):
    if payload is None:
        return False
    if file_stem == "pricing":
        return bool(build_site_records(payload, {}) or public_upstreams_have_available_models(payload))
    if file_stem == "topup":
        return bool(build_topup_plan_records(payload, None))
    if file_stem == "plans":
        return bool(build_topup_plan_records(None, payload))
    if file_stem == "user_groups":
        if isinstance(payload, dict):
            return bool(payload)
        if isinstance(payload, list):
            return bool(payload)
        return False
    return True


def _endpoint_result(raw_dir, file_stem, path, ok, reason=None, http_status=None, payload=None, **extra):
    result = {
        "path": path,
        "ok": ok,
        "saved": ok,
        "preserved_existing": (raw_dir / f"{file_stem}.json").exists() and not ok,
    }
    if http_status is not None:
        result["http_status"] = http_status
    if reason:
        result["reason"] = reason
    if payload is not None:
        result["payload_shape"] = _payload_shape(payload)
    result.update(extra)
    return result


def _fetch_one_site(
    session,
    base_url,
    site_name,
    site_root_dir=SITE_ROOT_DIR,
    new_api_user=None,
    token=None,
    authorization=None,
    endpoints=None,
):
    raw_dir = Path(site_root_dir) / site_name / "raw"
    auth_meta = apply_auth(session, new_api_user=new_api_user, token=token, authorization=authorization)
    meta = {
        "site_name": site_name,
        "base_url": base_url,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "auth_configured": auth_meta["new_api_user_configured"],
        "token_configured": auth_meta["token_configured"],
        "authorization_configured": auth_meta["authorization_configured"],
        "authorization_source": auth_meta["authorization_source"],
        "endpoints": {},
    }

    for file_stem, paths in (endpoints or ENDPOINTS).items():
        endpoint_base_url = base_url
        endpoint_paths = paths
        if file_stem == "pricing":
            endpoint_paths = _pricing_paths_for_site(base_url, site_name, paths)
        if site_name == "CCplus" and file_stem == "pricing":
            try:
                endpoint_base_url, ccplus_path = build_ccplus_channels_endpoint(base_url, authorization)
                endpoint_paths = [ccplus_path]
            except ValueError as exc:
                attempts = [_endpoint_result(raw_dir, file_stem, "/api/channels", False, reason="missing_auth", error=str(exc))]
                final_result = attempts[-1]
                final_result["attempts"] = attempts
                meta["endpoints"][file_stem] = final_result
                print(f"[-] {file_stem} 未获取到有效数据，保留已有 raw 文件")
                continue
        attempts = []
        for path in endpoint_paths:
            display_path = redact_endpoint_path(path)
            print(f"[*] 正在抓取 {display_path} ...")
            try:
                payload, status_code = fetch_json(session, endpoint_base_url, path, allow_statuses={401, 403})
                if status_code in {401, 403}:
                    attempts.append(
                        _endpoint_result(
                            raw_dir,
                            file_stem,
                            display_path,
                            False,
                            reason="auth_failed",
                            http_status=status_code,
                            auth_failed=True,
                            permission_failed=status_code == 403,
                        )
                    )
                    print(f"[-] 抓取 {display_path} 失败: 鉴权失败或权限不足")
                    break
                if not is_payload_meaningful(file_stem, payload):
                    attempts.append(
                        _endpoint_result(raw_dir, file_stem, display_path, False, reason="invalid_payload", http_status=status_code, payload=payload)
                    )
                    print(f"[-] 抓取 {display_path} 返回内容不足，继续尝试后续接口")
                    continue
                save_json(raw_dir / f"{file_stem}.json", payload)
                attempts.append(_endpoint_result(raw_dir, file_stem, display_path, True, http_status=status_code, payload=payload))
                print(f"[+] 已保存 {file_stem}.json")
                break
            except requests.exceptions.HTTPError as exc:
                response = getattr(exc, "response", None)
                status_code = getattr(response, "status_code", None)
                if status_code == 404:
                    attempts.append(_endpoint_result(raw_dir, file_stem, display_path, False, reason="not_found", http_status=status_code))
                    continue
                attempts.append(
                    _endpoint_result(
                        raw_dir,
                        file_stem,
                        display_path,
                        False,
                        reason="http_error",
                        http_status=status_code,
                        auth_failed=status_code in {401, 403},
                        permission_failed=status_code == 403,
                        error=str(exc),
                    )
                )
                print(f"[-] 抓取 {display_path} 失败: {exc}")
                break
            except Exception as exc:
                attempts.append(_endpoint_result(raw_dir, file_stem, display_path, False, reason="exception", error=str(exc)))
                print(f"[-] 抓取 {display_path} 失败: {exc}")
                break
        successful = next((attempt for attempt in attempts if attempt.get("ok")), None)
        final_result = dict(successful or (attempts[-1] if attempts else {"path": paths[-1], "ok": False, "saved": False, "reason": "未知错误"}))
        final_result["attempts"] = attempts
        meta["endpoints"][file_stem] = final_result
        if not final_result.get("ok"):
            print(f"[-] {file_stem} 未获取到有效数据，保留已有 raw 文件")

    save_json(raw_dir / "meta.json", meta)
    print(f"[+] 抓取结束，输出目录：{raw_dir}")
    return raw_dir, meta


def fetch_one_site(
    session,
    base_url,
    site_name,
    site_root_dir=SITE_ROOT_DIR,
    new_api_user=None,
    token=None,
    authorization=None,
):
    normalized_base_url = normalize_base_url(base_url)
    normalized_site_name = site_dir_name_from_url(normalized_base_url, site_name)
    return _fetch_one_site(
        session,
        normalized_base_url,
        normalized_site_name,
        site_root_dir,
        new_api_user=new_api_user,
        token=token,
        authorization=authorization,
    )


def main():
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
        raw_new_api_user = input("请输入 new-api-user 登录态（可直接回车跳过）: ").strip()
        raw_token = input("请输入 token（可直接回车跳过）: ").strip()
        raw_authorization = input("请输入完整 Authorization 请求头（可直接回车跳过）: ").strip()

        session = _build_session()
        site_name = site_dir_name_from_url(base_url, raw_input_site_name)
        _fetch_one_site(
            session,
            base_url,
            site_name,
            SITE_ROOT_DIR,
            new_api_user=raw_new_api_user,
            token=raw_token,
            authorization=raw_authorization,
        )


if __name__ == "__main__":
    raise SystemExit(main())
