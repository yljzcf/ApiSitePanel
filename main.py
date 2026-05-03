import contextlib
import io
import json
import socket
import sys
import threading
import webbrowser
from datetime import datetime
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, urlunparse

SCRIPT_DIR = Path(__file__).resolve().parent / "script"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from clean_site_raw import clean_one_site
from fetch_site_raw import _build_session, fetch_one_site
from site_api_utils import load_json, normalize_base_url, sanitize_path_component, save_json, site_dir_name_from_url


AUTH_FIELD_NAMES = ("Authorization", "new-api-user", "token")


PROJECT_ROOT = Path(__file__).resolve().parent
SITE_ROOT_DIR = PROJECT_ROOT / "site"
DEFAULT_CONFIG_NAME = "site.json"
TOPUP_PLANS_NAME = "topup+plans.json"
DATA_LABELS = {
    "models": "模型",
    "groups": "分组",
    "topup": "充值",
    "plans": "订阅",
}
MISSING_TO_DATA_KEYS = {
    "models": ("models",),
    "groups": ("groups",),
    "topup": ("topup",),
    "plans": ("plans",),
    "topup_or_plans": ("topup", "plans"),
}
DEFAULT_SITE_CONFIG_TEMPLATE = [
    {
        "url": "https://api.example.com",
        "name": "示例站点",
        "new-api-user": "",
    }
]


def ensure_site_config(config_path):
    config_path = Path(config_path)
    if config_path.exists():
        return False
    save_json(config_path, DEFAULT_SITE_CONFIG_TEMPLATE)
    return True


def ensure_topup_plans_file(path):
    path = Path(path)
    if path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("[]\n", encoding="utf-8")
    return True


def validate_topup_plans_file(path):
    path = Path(path)
    try:
        with path.open("r", encoding="utf-8") as file:
            payload = json.load(file)
    except json.JSONDecodeError as exc:
        return f"JSON 格式错误：第 {exc.lineno} 行第 {exc.colno} 列，{exc.msg}。"
    if not isinstance(payload, list):
        return f"{TOPUP_PLANS_NAME} 顶层必须是单个站点的充值/套餐数组。"
    return None


def wait_for_topup_plans_ready(paths, input_func=input, output_func=print):
    paths = [Path(path) for path in paths]
    for path in paths:
        ensure_topup_plans_file(path)
    errors = [(path, validate_topup_plans_file(path)) for path in paths]
    errors = [(path, error) for path, error in errors if error is not None]
    for path, error in errors:
        output_func(f"[-] {path}：{error}")
    return not errors


def format_data_tags(keys):
    ordered_keys = [key for key in DATA_LABELS if key in set(keys or [])]
    if not ordered_keys:
        return "(0项)"
    return "".join(f"<{DATA_LABELS[key]}>" for key in ordered_keys)


def format_local_data_items(items):
    parts = []
    for item in items or []:
        date = item.get("date")
        suffix = f"({date})" if date else ""
        for key in DATA_LABELS:
            if key in set(item.get("keys") or []):
                parts.append(f"<{DATA_LABELS[key]}>{suffix}")
    return "".join(parts) if parts else "(0项)"


def format_file_tags(files, empty_label="无"):
    files = [file for file in files or [] if file]
    if not files:
        return empty_label

    parts = []
    for file in files:
        if isinstance(file, dict):
            name = file.get("name")
            label = file.get("label")
            if not name:
                continue
            suffix = f"({label})" if label else ""
            parts.append(f"<{name}{suffix}>")
        else:
            parts.append(f"<{file}>")
    return "".join(parts) if parts else empty_label


def file_date_stamp(path):
    path = Path(path)
    if not path.exists():
        return None
    return datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d")


def diagnostic_missing_keys(diagnostic):
    keys = []
    for missing in (diagnostic or {}).get("missing") or []:
        for key in MISSING_TO_DATA_KEYS.get(missing, (missing,)):
            if key in DATA_LABELS and key not in keys:
                keys.append(key)
    return keys


def summary_interface_keys(summary):
    keys = []
    summary = summary or {}
    if summary.get("model_count", 0) > 0:
        keys.append("models")
    if summary.get("group_count", 0) > 0:
        keys.append("groups")
    has_topup_plans = summary.get("topup_plan_count", 0) > 0
    uses_existing_topup_plans = (
        summary.get("topup_plans_existing_count", 0) > 0
        and not summary.get("topup_plans_auto_generated", False)
    )
    if has_topup_plans and not uses_existing_topup_plans:
        keys.extend(["topup", "plans"])
    return keys


def local_topup_keys(topup_path):
    topup_path = Path(topup_path)
    if not topup_path.exists() or validate_topup_plans_file(topup_path) is not None:
        return [], None
    payload = load_json(topup_path)
    if not isinstance(payload, list) or not payload:
        return [], None

    keys = []
    for record in payload:
        if not isinstance(record, dict):
            continue
        record_type = str(record.get("类型") or "")
        if "充值" in record_type and "topup" not in keys:
            keys.append("topup")
        if ("套餐" in record_type or "订阅" in record_type) and "plans" not in keys:
            keys.append("plans")

    if not keys:
        keys.append("topup")
    return keys, file_date_stamp(topup_path)


def local_topup_file_tag(site, site_root_dir=SITE_ROOT_DIR):
    site_dir = Path(site_root_dir) / site_dir_name_from_url(site["url"], site["name"])
    topup_path = site_dir / TOPUP_PLANS_NAME
    keys, _ = local_topup_keys(topup_path)
    if keys:
        return {"name": TOPUP_PLANS_NAME, "label": "本地版"}
    return None


def local_cleaned_files(site, site_root_dir=SITE_ROOT_DIR):
    site_name = site["name"]
    site_dir = Path(site_root_dir) / site_dir_name_from_url(site["url"], site_name)
    output_file = f"{sanitize_path_component(site_name, fallback=site_dir.name)}.json"
    cleaned_files = []
    if (site_dir / output_file).exists():
        cleaned_files.append(output_file)
    topup_file = local_topup_file_tag(site, site_root_dir)
    if topup_file:
        cleaned_files.append(topup_file)
    return cleaned_files


def topup_plans_cleaned_file(summary):
    if summary.get("topup_plans_existing_count", 0) > 0 and not summary.get("topup_plans_auto_generated", False):
        return {"name": TOPUP_PLANS_NAME, "label": "本地版"}
    if summary.get("topup_plan_count", 0) > 0:
        return TOPUP_PLANS_NAME
    return None


def build_site_task_record(site, pipeline_result, site_root_dir=SITE_ROOT_DIR):
    diagnostics = _diagnostic_by_site((pipeline_result or {}).get("diagnostics", []))
    summaries = {summary.get("site_name"): summary for summary in (pipeline_result or {}).get("success_summaries", [])}
    site_name = site["name"]
    summary = summaries.get(site_name)
    diagnostic = diagnostics.get(site_name, {})
    all_keys = list(DATA_LABELS)
    interface_keys = summary_interface_keys(summary)
    local_items = []
    local_keys = []

    def remaining(keys):
        occupied = set(interface_keys) | set(local_keys)
        return [key for key in keys if key in DATA_LABELS and key not in occupied]

    fallback = diagnostic.get("local_fallback") or local_site_output_fallback(site, site_root_dir)
    if fallback.get("available"):
        fallback_keys = remaining(all_keys)
        if fallback_keys:
            local_items.append({"keys": fallback_keys, "date": fallback.get("updated_at")})
            local_keys.extend(fallback_keys)

    site_dir = Path(site_root_dir) / site_dir_name_from_url(site["url"], site["name"])
    topup_keys, topup_date = local_topup_keys(site_dir / TOPUP_PLANS_NAME)
    topup_keys = remaining(topup_keys)
    if topup_keys:
        local_items.append({"keys": topup_keys, "date": topup_date})
        local_keys.extend(topup_keys)

    missing_keys = [key for key in all_keys if key not in set(interface_keys) and key not in set(local_keys)]
    output_file = summary.get("output_file") if summary is not None else None
    output_file = output_file or f"{sanitize_path_component(site_name, fallback=site_dir.name)}.json"
    topup_cleaned_file = topup_plans_cleaned_file(summary or {}) if summary is not None else local_topup_file_tag(site, site_root_dir)
    cleaned_files = []
    uncleaned_files = []

    if summary is not None or (site_dir / output_file).exists():
        cleaned_files.append(output_file)
    else:
        uncleaned_files.append(output_file)

    if topup_cleaned_file:
        cleaned_files.append(topup_cleaned_file)
    else:
        uncleaned_files.append(TOPUP_PLANS_NAME)

    return {
        "site_name": site_name,
        "interface_keys": interface_keys,
        "local_items": local_items,
        "missing_keys": missing_keys,
        "cleaned_files": cleaned_files,
        "uncleaned_files": uncleaned_files,
        "complete": not missing_keys,
    }


def print_site_task_tree(sites, pipeline_result, site_root_dir=SITE_ROOT_DIR, output_func=print):
    records = [build_site_task_record(site, pipeline_result, site_root_dir=site_root_dir) for site in sites]
    for index, record in enumerate(records):
        if index:
            output_func("")
        output_func(f"{record['site_name']}---接口获得{format_data_tags(record['interface_keys'])}")
        output_func(f"\\--本地获得{format_local_data_items(record['local_items'])}")
        output_func(f"\\--缺失数据{format_data_tags(record['missing_keys'])}")
        output_func(f"\\--已清洗：{format_file_tags(record['cleaned_files'])}")
        output_func(f"\\--未清洗：{format_file_tags(record['uncleaned_files'])}")


def topup_plan_paths_for_summaries(summaries, site_root_dir=SITE_ROOT_DIR):
    return [Path(site_root_dir) / summary["site_name"] / TOPUP_PLANS_NAME for summary in summaries]


def find_available_port(preferred=8000, attempts=50):
    for port in range(preferred, preferred + attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise OSError(f"无法在 {preferred}-{preferred + attempts - 1} 范围内找到可用端口。")


class QuietSimpleHTTPRequestHandler(SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        return


def start_panel_server(project_root, output_func=print):
    project_root = Path(project_root)
    port = find_available_port()
    handler = partial(QuietSimpleHTTPRequestHandler, directory=str(project_root))
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{port}/panel/"
    output_func(f"[+] 前端服务已启动：{url}")
    return server, thread, url


def open_panel(url, output_func=print):
    webbrowser.open(url)
    output_func(f"[+] 已尝试自动打开浏览器：{url}")


def _is_placeholder_site(site):
    return site.get("url") == "https://api.example.com" and site.get("name") == "示例站点"


def normalize_site_entry(site):
    if not isinstance(site, dict):
        raise ValueError("站点配置必须是对象")
    name = str(site.get("name") or "").strip()
    if not name:
        raise ValueError("站点名不能为空")
    url = normalize_base_url(site.get("url"))
    normalized = {"url": url, "name": name}
    new_api_user = str(site.get("new-api-user") or "").strip()
    if new_api_user:
        normalized["new-api-user"] = new_api_user
    token = str(site.get("token") or "").strip()
    if token:
        normalized["token"] = token
    authorization = str(site.get("Authorization") or site.get("authorization") or "").strip()
    if authorization:
        normalized["Authorization"] = authorization
    return normalized


def raw_dir_for_site(site, site_root_dir=SITE_ROOT_DIR):
    return Path(site_root_dir) / site_dir_name_from_url(site["url"], site["name"]) / "raw"


def safe_url_for_display(parsed_url):
    netloc = parsed_url.hostname or ""
    if parsed_url.port:
        netloc = f"{netloc}:{parsed_url.port}"
    if parsed_url.username or parsed_url.password:
        netloc = f"***@{netloc}"
    query = "***" if parsed_url.query else ""
    fragment = "***" if parsed_url.fragment else ""
    return urlunparse((parsed_url.scheme, netloc, parsed_url.path, "", query, fragment))


def _site_auth_fields(site):
    return [field for field in AUTH_FIELD_NAMES if site.get(field)]


def check_site_config(sites):
    report = {"site_count": len(sites), "sites": [], "warnings": [], "errors": []}
    seen_urls = {}
    seen_names = {}
    seen_directories = {}

    for index, site in enumerate(sites, start=1):
        directory = site_dir_name_from_url(site["url"], site["name"])
        parsed_url = urlparse(site["url"])
        item = {
            "index": index,
            "name": site["name"],
            "url": safe_url_for_display(parsed_url),
            "directory": directory,
            "raw_dir": str(Path(SITE_ROOT_DIR.name) / directory / "raw"),
            "auth_fields": _site_auth_fields(site),
        }
        report["sites"].append(item)

        for seen, key, label in (
            (seen_urls, site["url"], "URL"),
            (seen_names, site["name"], "站点名"),
            (seen_directories, directory, "输出目录"),
        ):
            previous_index = seen.get(key)
            if previous_index is not None:
                target = report["errors"] if label == "输出目录" else report["warnings"]
                target.append(f"{label}重复：{key}（site.json[{previous_index}] 与 site.json[{index}]）")
            else:
                seen[key] = index

    return report


def has_blocking_site_config_errors(report):
    return bool(report.get("errors"))


def print_site_config_report(report, output_func=print):
    output_func(f"[*] 站点配置整体检查：共 {report['site_count']} 个站点。")
    for item in report["sites"]:
        auth_label = ", ".join(item["auth_fields"]) if item["auth_fields"] else "无认证字段"
        output_func(
            f"    [{item['index']}] {item['name']} -> {item['url']} | raw: {item['raw_dir']} | auth: {auth_label}"
        )
    for warning in report["warnings"]:
        output_func(f"[!] {warning}")
    for error in report.get("errors", []):
        output_func(f"[-] {error}")


def load_site_configs(config_path):
    config_path = Path(config_path)
    ensure_site_config(config_path)
    payload = load_json(config_path)
    if not isinstance(payload, list):
        raise ValueError("站点配置文件必须是数组")

    usable_sites = []
    for index, item in enumerate(payload, start=1):
        try:
            normalized = normalize_site_entry(item)
        except ValueError as exc:
            raise ValueError(f"site.json[{index}]：{exc}") from exc
        if _is_placeholder_site(normalized):
            continue
        usable_sites.append(normalized)
    return usable_sites


def ask_yes_no(prompt, input_func=input, output_func=print, default=False):
    hint = "Y/n" if default else "y/N"
    while True:
        answer = input_func(f"{prompt} [{hint}]: ").strip().lower()
        if not answer:
            return default
        if answer in {"y", "yes"}:
            return True
        if answer in {"n", "no"}:
            return False
        output_func("[-] 请输入 y 或 n。")


def choose_start_mode(input_func=input, output_func=print):
    while True:
        answer = input_func("请选择启动方式：1=爬取数据，2=打开面板: ").strip()
        if answer == "1":
            return "crawl"
        if answer == "2":
            return "panel"
        output_func("[-] 请输入 1 或 2。")


def choose_site_filter(input_func=input):
    return input_func("请输入要单独处理的站点名（直接回车处理全部）: ").strip()


def choose_config_target(default_config_path, input_func=input, output_func=print):
    while True:
        answer = input_func("请选择站点配置写入方式：1=追加到原 site.json，2=单独新建配置文件: ").strip()
        if answer == "1":
            return Path(default_config_path)
        if answer == "2":
            while True:
                file_name = input_func("请输入新的配置文件名（例如 site-dev.json）: ").strip()
                if not file_name:
                    output_func("[-] 配置文件名不能为空。")
                    continue
                if not file_name.endswith(".json"):
                    output_func("[-] 配置文件名必须以 .json 结尾。")
                    continue
                if Path(file_name).name != file_name:
                    output_func("[-] 配置文件名不能包含路径。")
                    continue
                return Path(default_config_path).parent / file_name
        output_func("[-] 请输入 1 或 2。")


def collect_new_sites(existing_sites=None, input_func=input, output_func=print):
    sites = []
    known_urls = {site["url"] for site in (existing_sites or [])}
    while True:
        raw_url = input_func("请输入站点 URL，直接回车结束新增: ").strip()
        if not raw_url:
            break
        try:
            url = normalize_base_url(raw_url)
        except ValueError as exc:
            output_func(f"[-] {exc}")
            continue
        if url in known_urls:
            output_func(f"[-] 站点 URL 已存在或重复：{url}")
            continue

        name = input_func("请输入站点名: ").strip()
        if not name:
            output_func("[-] 站点名不能为空。")
            continue
        dir_name = site_dir_name_from_url(url, name)
        new_api_user = input_func("请输入 new-api-user 登录态（可直接回车跳过）: ").strip()
        token = input_func("请输入 token（可直接回车跳过）: ").strip()
        entry = {"url": url, "name": name}
        if new_api_user:
            entry["new-api-user"] = new_api_user
        if token:
            entry["token"] = token
        authorization = input_func("请输入完整 Authorization 请求头（可直接回车跳过）: ").strip()
        if authorization:
            entry["Authorization"] = authorization
        sites.append(entry)
        known_urls.add(url)
        output_func(f"[+] 已记录站点：{name}（目录名：{dir_name}）")
    return sites


def filter_sites_by_name(sites, site_name):
    requested_name = str(site_name or "").strip()
    if not requested_name:
        return list(sites)
    selected = [site for site in sites if site.get("name") == requested_name]
    if not selected:
        raise ValueError(f"未找到指定站点：{requested_name}")
    return selected


def resolve_sites_for_run(default_config_path, input_func=input, output_func=print):
    default_config_path = Path(default_config_path)
    existing_sites = load_site_configs(default_config_path)
    if existing_sites:
        use_existing = ask_yes_no(
            f"检测到 {default_config_path.name} 中已有 {len(existing_sites)} 个站点，是否直接使用这些配置继续抓取？",
            input_func=input_func,
            output_func=output_func,
            default=True,
        )
        if use_existing:
            return default_config_path, existing_sites
        target_config_path = choose_config_target(default_config_path, input_func=input_func, output_func=output_func)
    else:
        target_config_path = default_config_path

    new_sites = collect_new_sites(existing_sites=existing_sites, input_func=input_func, output_func=output_func)
    if new_sites:
        sites_to_save = new_sites
        if target_config_path == default_config_path and existing_sites:
            sites_to_save = existing_sites + new_sites
        save_json(target_config_path, sites_to_save)
    return target_config_path, new_sites


def render_progress(current, total, label):
    total = max(total, 1)
    width = 24
    filled = int(width * current / total)
    bar = "#" * filled + "-" * (width - filled)
    return f"[{bar}] {current}/{total} {label}"


def build_totals(summaries):
    return {
        "site_count": len(summaries),
        "model_count": sum(summary.get("model_count", 0) for summary in summaries),
        "group_count": sum(summary.get("group_count", 0) for summary in summaries),
        "record_count": sum(summary.get("record_count", 0) for summary in summaries),
    }


def local_site_output_fallback(site, site_root_dir=SITE_ROOT_DIR):
    site_dir = Path(site_root_dir) / site_dir_name_from_url(site["url"], site["name"])
    output_path = site_dir / f"{site_dir_name_from_url(site['url'], site['name'])}.json"
    if not output_path.exists():
        return {"available": False}
    updated_at = datetime.fromtimestamp(output_path.stat().st_mtime).strftime("%Y-%m-%d")
    return {"available": True, "path": str(output_path), "updated_at": updated_at}


def _fetch_one_site_quietly(*args, **kwargs):
    with contextlib.redirect_stdout(io.StringIO()):
        return fetch_one_site(*args, **kwargs)


def _clean_one_site_quietly(*args, **kwargs):
    with contextlib.redirect_stdout(io.StringIO()):
        return clean_one_site(*args, **kwargs)


def _diagnostic_by_site(diagnostics):
    return {diagnostic.get("site_name"): diagnostic for diagnostic in diagnostics if isinstance(diagnostic, dict)}


def _failure_reason_for_site(site_name, pipeline_result):
    diagnostic = _diagnostic_by_site(pipeline_result.get("diagnostics", [])).get(site_name, {})
    issues = diagnostic.get("issues") or []
    if issues:
        return "、".join(issues)
    for item in pipeline_result.get("fetch_failed", []):
        if item.get("site_name") == site_name:
            return item.get("error") or item.get("reason") or "抓取失败"
    for item in pipeline_result.get("clean_failed", []):
        if item.get("site_name") == site_name:
            return item.get("error") or item.get("reason") or "清洗失败"
    return "未知原因"


def _failed_site_names(pipeline_result):
    names = []
    for key in ("fetch_failed", "clean_failed"):
        for item in pipeline_result.get(key, []):
            site_name = item.get("site_name")
            if site_name and site_name not in names:
                names.append(site_name)
    return names


def print_crawl_summary(pipeline_result, output_func=print):
    success_count = len(pipeline_result.get("success_summaries", []))
    failed_count = len(_failed_site_names(pipeline_result))
    processed = success_count + failed_count
    return {"processed": processed, "success": success_count, "failed": failed_count}


def has_panel_usable_results(pipeline_result):
    if pipeline_result.get("success_summaries"):
        return True
    return any(
        isinstance(diagnostic, dict) and diagnostic.get("local_fallback", {}).get("available")
        for diagnostic in pipeline_result.get("diagnostics", [])
    )

def build_site_diagnostics(site, fetch_meta, summary, site_root_dir=SITE_ROOT_DIR):
    endpoints = fetch_meta.get("endpoints", {}) if isinstance(fetch_meta, dict) else {}
    issues = []
    missing = []
    permission_endpoints = []

    for name, endpoint in endpoints.items():
        if not isinstance(endpoint, dict):
            continue
        if endpoint.get("auth_failed") or endpoint.get("permission_failed"):
            permission_endpoints.append(
                {
                    "name": name,
                    "path": endpoint.get("path"),
                    "http_status": endpoint.get("http_status"),
                }
            )
    if permission_endpoints:
        issues.append("权限不足")

    if summary.get("record_count", 0) == 0:
        issues.append("模型/分组信息不足")
        missing.extend(["models", "groups"])
    else:
        if summary.get("model_count", 0) == 0:
            issues.append("模型信息不足")
            missing.append("models")
        if summary.get("group_count", 0) == 0:
            issues.append("分组信息不足")
            missing.append("groups")

    if summary.get("topup_plan_count", 0) == 0 and summary.get("topup_plans_existing_count", 0) == 0:
        issues.append("充值/套餐信息不足")
        missing.append("topup_or_plans")

    return {
        "site_name": site["name"],
        "auth_fields": _site_auth_fields(site),
        "issues": issues,
        "missing": missing,
        "permission_endpoints": permission_endpoints,
        "raw_dir": str(raw_dir_for_site(site, site_root_dir)),
        "tried_endpoints": [
            {"name": name, "path": endpoint.get("path"), "ok": endpoint.get("ok")}
            for name, endpoint in endpoints.items()
            if isinstance(endpoint, dict)
        ],
    }


def format_site_diagnostic_messages(diagnostic):
    if not diagnostic.get("issues"):
        return []
    messages = [f"[-] {diagnostic['site_name']}：{'、'.join(diagnostic['issues'])}。"]
    auth_fields = diagnostic.get("auth_fields") or []
    messages.append(f"    - 当前认证字段：{', '.join(auth_fields) if auth_fields else '无'}")
    if diagnostic.get("permission_endpoints"):
        messages.append("    - 权限不足端点：")
        for endpoint in diagnostic["permission_endpoints"]:
            messages.append(f"      * {endpoint['name']} {endpoint.get('path')} HTTP {endpoint.get('http_status')}")
        messages.append("    - 请从已授权账号的浏览器开发者工具 Network 中补充对应接口的 Authorization、token 或 New-Api-User。")
    if diagnostic.get("missing"):
        tried = ", ".join(
            f"{endpoint['name']}:{endpoint.get('path')}" for endpoint in diagnostic.get("tried_endpoints", [])
        )
        messages.append(f"    - 信息不足，可补充 raw JSON 或继续探查接口；已尝试：{tried or '无'}")
        messages.append(f"    - raw 目录：{diagnostic.get('raw_dir')}")
    return messages


def run_pipeline(sites, site_root_dir=SITE_ROOT_DIR, output_func=print):
    result = {
        "success_summaries": [],
        "fetch_failed": [],
        "clean_failed": [],
        "diagnostics": [],
    }
    total = len(sites)
    for index, site in enumerate(sites, start=1):
        session = _build_session()
        output_func(render_progress(index - 1, total, f"准备处理 {site['name']}"))
        try:
            raw_dir, fetch_result = _fetch_one_site_quietly(
                session,
                site["url"],
                site["name"],
                site_root_dir,
                new_api_user=site.get("new-api-user"),
                token=site.get("token"),
                authorization=site.get("Authorization"),
            )
        except Exception as exc:
            output_func(render_progress(index, total, f"{site['name']} 接口未完成"))
            failed_raw_dir = raw_dir_for_site(site, site_root_dir)
            result["fetch_failed"].append(
                {"site_name": site["name"], "reason": "fetch_exception", "error": str(exc), "raw_dir": failed_raw_dir}
            )
            diagnostic = {
                "site_name": site["name"],
                "auth_fields": _site_auth_fields(site),
                "issues": ["抓取失败"],
                "missing": ["models", "groups", "topup_or_plans"],
                "permission_endpoints": [],
                "raw_dir": str(failed_raw_dir),
                "tried_endpoints": [],
            }
            diagnostic["local_fallback"] = local_site_output_fallback(site, site_root_dir)
            result["diagnostics"].append(diagnostic)
            continue

        pricing_endpoint = fetch_result.get("endpoints", {}).get("pricing", {})
        pricing_auth_failed = isinstance(pricing_endpoint, dict) and pricing_endpoint.get("auth_failed")
        if pricing_auth_failed:
            output_func(render_progress(index, total, f"{site['name']} 接口不可用，尝试本地清洗"))
            result["fetch_failed"].append({"site_name": site["name"], "reason": "auth_failed", "raw_dir": raw_dir})

        site_dir = raw_dir.parent
        try:
            output_path, summary = _clean_one_site_quietly(site_dir, raw_dir, output_name=site["name"])
        except Exception as exc:
            output_func(render_progress(index, total, f"{site['name']} 清洗未完成"))
            result["clean_failed"].append(
                {"site_name": site["name"], "reason": "clean_exception", "error": str(exc)}
            )
            endpoints = fetch_result.get("endpoints", {}) if isinstance(fetch_result, dict) else {}
            diagnostic = {
                "site_name": site["name"],
                "auth_fields": _site_auth_fields(site),
                "issues": ["清洗失败"],
                "missing": [],
                "permission_endpoints": [],
                "raw_dir": str(raw_dir),
                "tried_endpoints": [
                    {"name": name, "path": endpoint.get("path"), "ok": endpoint.get("ok")}
                    for name, endpoint in endpoints.items()
                    if isinstance(endpoint, dict)
                ],
            }
            diagnostic["local_fallback"] = local_site_output_fallback(site, site_root_dir)
            result["diagnostics"].append(diagnostic)
            continue

        result["success_summaries"].append(summary)
        diagnostic = build_site_diagnostics(site, fetch_result, summary, site_root_dir=site_root_dir)
        diagnostic["local_fallback"] = local_site_output_fallback(site, site_root_dir)
        result["diagnostics"].append(diagnostic)
        output_func(render_progress(index, total, f"已完成 {site['name']} -> {output_path.name}"))
    return result


def retry_failed_fetch_sites(fetch_failed, site_root_dir=SITE_ROOT_DIR, output_func=print):
    summaries = []
    for item in fetch_failed:
        site_name = item["site_name"]
        raw_dir = Path(item.get("raw_dir") or (Path(site_root_dir) / site_name / "raw"))
        site_dir = raw_dir.parent
        pricing_path = raw_dir / "pricing.json"
        if not pricing_path.exists():
            output_func(f"[-] {site_name} 缺少 pricing.json：{pricing_path}")
            continue
        try:
            _, summary = clean_one_site(site_dir, raw_dir, output_name=site_name)
        except Exception as exc:
            output_func(f"[-] {site_name} 重新清洗未完成：{exc}")
            continue
        summaries.append(summary)
    return summaries


def handle_failed_fetch_retry(fetch_failed, clean_failed, input_func=input, output_func=print, site_root_dir=SITE_ROOT_DIR):
    if clean_failed:
        output_func("[-] 以下站点清洗未完成，当前不支持手动补 raw：")
        for item in clean_failed:
            output_func(f"    - {item['site_name']}：{item.get('error', item.get('reason', 'unknown'))}")

    if not fetch_failed:
        return []

    output_func("[-] 以下站点接口未完成，可手动将 JSON 文件补到对应 raw 目录后再尝试清洗：")
    for item in fetch_failed:
        output_func(f"    - {item['site_name']} -> raw 目录：{item['raw_dir']}")

    should_retry = ask_yes_no(
        "是否要手动添加 json 文件到这些站点的 raw 目录后再尝试清洗？",
        input_func=input_func,
        output_func=output_func,
        default=False,
    )
    if not should_retry:
        return []

    finished = ask_yes_no(
        "是否已经完成添加？",
        input_func=input_func,
        output_func=output_func,
        default=False,
    )
    if not finished:
        return []

    return retry_failed_fetch_sites(fetch_failed, site_root_dir=site_root_dir, output_func=output_func)


def ask_continue_or_exit(input_func=input, output_func=print):
    output_func("[*] 本轮任务已结束。")
    return bool(input_func("[?] 输入任意内容继续新增/抓取站点；直接回车结束程序: ").strip())


def print_summary(summaries, output_func=print):
    return build_totals(summaries)


def start_panel_flow(output_func=print):
    _, _, panel_url = start_panel_server(PROJECT_ROOT, output_func=output_func)
    open_panel(panel_url, output_func=output_func)


def wait_for_panel_exit(input_func=input):
    input_func("[*] 面板已打开。请在浏览器中使用面板；完成后回到此窗口按回车退出...")


def pause_before_exit(input_func=input, output_func=print, enabled=True):
    if not enabled:
        return
    output_func("")
    try:
        input_func("[*] 程序即将退出，按回车关闭窗口...")
    except EOFError:
        return


def main(input_func=input, output_func=print):
    exit_code = 0

    while True:
        start_mode = choose_start_mode(input_func=input_func, output_func=output_func)
        if start_mode == "panel":
            start_panel_flow(output_func=output_func)
            output_func("[*] 当前终端不会自动关闭。")
            wait_for_panel_exit(input_func=input_func)
            return exit_code

        config_path = PROJECT_ROOT / DEFAULT_CONFIG_NAME
        output_func("[*] 正在读取站点配置...")
        target_config_path, sites = resolve_sites_for_run(
            config_path,
            input_func=input_func,
            output_func=output_func,
        )
        output_func(f"[*] 本次使用配置文件：{target_config_path}")
        only_site = choose_site_filter(input_func=input_func)
        if only_site:
            try:
                sites = filter_sites_by_name(sites, only_site)
            except ValueError as exc:
                exit_code = 1
                output_func(f"[-] {exc}")
                return exit_code
            output_func(f"[*] 本次仅处理站点：{only_site}")
        site_config_report = check_site_config(sites)
        print_site_config_report(site_config_report, output_func=output_func)
        if has_blocking_site_config_errors(site_config_report):
            exit_code = 1
            output_func("[-] 站点配置存在阻塞性错误，请修正后重试。")
            return exit_code

        all_summaries = []
        if not sites:
            output_func("[-] 当前没有可抓取站点。")
        else:
            output_func("[*] 开始抓取与清洗...")
            pipeline_result = run_pipeline(sites, site_root_dir=SITE_ROOT_DIR, output_func=output_func)
            if pipeline_result.get("success_summaries"):
                wait_for_topup_plans_ready(
                    topup_plan_paths_for_summaries(pipeline_result["success_summaries"], site_root_dir=SITE_ROOT_DIR),
                    input_func=input_func,
                    output_func=output_func,
                )
            print_site_task_tree(sites, pipeline_result, site_root_dir=SITE_ROOT_DIR, output_func=output_func)
            panel_usable = has_panel_usable_results(pipeline_result)
            if panel_usable:
                start_panel_flow(output_func=output_func)
            else:
                exit_code = 1
                output_func("[-] 本次没有可用于面板的数据。")

        output_func("[*] 当前终端不会自动关闭。")
        if not ask_continue_or_exit(input_func=input_func, output_func=output_func):
            return exit_code


def run_cli(input_func=input, output_func=print, pause=True):
    exit_code = 0
    try:
        exit_code = main(input_func=input_func, output_func=output_func)
    except EOFError:
        exit_code = 1
        output_func("[-] 输入流已关闭，程序无法继续交互。")
    except KeyboardInterrupt:
        exit_code = 130
        output_func("")
        output_func("[-] 用户中断，程序已停止。")
    except Exception as exc:
        exit_code = 1
        output_func(f"[-] 程序发生未处理错误：{type(exc).__name__}: {exc}")
    finally:
        pause_before_exit(input_func=input_func, output_func=output_func, enabled=pause)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(run_cli())
