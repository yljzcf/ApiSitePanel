import json
import socket
import sys
import threading
import webbrowser
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent / "script"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from clean_site_raw import clean_one_site
from fetch_site_raw import _build_session, fetch_one_site
from site_api_utils import load_json, normalize_base_url, save_json, site_dir_name_from_url


PROJECT_ROOT = Path(__file__).resolve().parent
SITE_ROOT_DIR = PROJECT_ROOT / "site"
DEFAULT_CONFIG_NAME = "site.json"
TOPUP_PLANS_NAME = "topup+plans.json"
DEFAULT_SITE_CONFIG_TEMPLATE = [
    {
        "url": "https://api.example.com",
        "name": "示例站点",
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
    path.write_text("{}\n", encoding="utf-8")
    return True


def validate_topup_plans_file(path):
    path = Path(path)
    try:
        with path.open("r", encoding="utf-8") as file:
            payload = json.load(file)
    except json.JSONDecodeError as exc:
        return f"JSON 格式错误：第 {exc.lineno} 行第 {exc.colno} 列，{exc.msg}。"
    if not isinstance(payload, dict):
        return f"{TOPUP_PLANS_NAME} 顶层必须是按站点名分组的对象。"
    return None


def wait_for_topup_plans_ready(path, input_func=input, output_func=print):
    path = Path(path)
    ensure_topup_plans_file(path)
    output_func(f"[*] 基础数据已生成，请在 {path} 中补充充值/套餐信息。")
    while True:
        ready = ask_yes_no("是否已经添加完成？", input_func=input_func, output_func=output_func, default=False)
        if not ready:
            output_func("[-] 未确认完成，暂不打开前端面板。")
            return False
        error = validate_topup_plans_file(path)
        if error is None:
            return True
        output_func(f"[-] {error}")
        output_func(f"[*] 请修正 {path} 后再次确认。")


def find_available_port(preferred=8000, attempts=50):
    for port in range(preferred, preferred + attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise OSError(f"无法在 {preferred}-{preferred + attempts - 1} 范围内找到可用端口。")


def start_panel_server(project_root, output_func=print):
    project_root = Path(project_root)
    port = find_available_port()
    handler = partial(SimpleHTTPRequestHandler, directory=str(project_root))
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
    return {"url": url, "name": name}


def load_site_configs(config_path):
    config_path = Path(config_path)
    ensure_site_config(config_path)
    payload = load_json(config_path)
    if not isinstance(payload, list):
        raise ValueError("站点配置文件必须是数组")

    usable_sites = []
    for item in payload:
        normalized = normalize_site_entry(item)
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
        sites.append({"url": url, "name": name})
        known_urls.add(url)
        output_func(f"[+] 已记录站点：{name}（目录名：{dir_name}）")
    return sites


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


def run_pipeline(sites, site_root_dir=SITE_ROOT_DIR, output_func=print):
    session = _build_session()
    result = {
        "success_summaries": [],
        "fetch_failed": [],
        "clean_failed": [],
    }
    total = len(sites)
    for index, site in enumerate(sites, start=1):
        output_func(render_progress(index - 1, total, f"准备处理 {site['name']}"))
        try:
            raw_dir, fetch_result = fetch_one_site(
                session,
                site["url"],
                site["name"],
                site_root_dir,
            )
        except Exception as exc:
            output_func(render_progress(index, total, f"{site['name']} 抓取失败"))
            output_func(f"[-] {site['name']} 抓取失败：{exc}")
            failed_raw_dir = Path(site_root_dir) / site_dir_name_from_url(site["url"], site["name"]) / "raw"
            result["fetch_failed"].append(
                {"site_name": site["name"], "reason": "fetch_exception", "error": str(exc), "raw_dir": failed_raw_dir}
            )
            continue

        if any(endpoint.get("auth_failed") for endpoint in fetch_result.get("endpoints", {}).values() if isinstance(endpoint, dict)):
            output_func(render_progress(index, total, f"{site['name']} 鉴权失败"))
            output_func(f"[-] {site['name']} 鉴权失败：请检查 token 或登录状态。")
            result["fetch_failed"].append({"site_name": site["name"], "reason": "auth_failed", "raw_dir": raw_dir})
            continue

        site_dir = raw_dir.parent
        try:
            output_path, summary = clean_one_site(site_dir, raw_dir, output_name=site["name"])
        except Exception as exc:
            output_func(render_progress(index, total, f"{site['name']} 清洗失败"))
            output_func(f"[-] {site['name']} 清洗失败：{exc}")
            result["clean_failed"].append(
                {"site_name": site["name"], "reason": "clean_exception", "error": str(exc)}
            )
            continue

        result["success_summaries"].append(summary)
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
            output_func(f"[-] {site_name} 重新清洗失败：{exc}")
            continue
        summaries.append(summary)
    return summaries


def handle_failed_fetch_retry(fetch_failed, clean_failed, input_func=input, output_func=print, site_root_dir=SITE_ROOT_DIR):
    if clean_failed:
        output_func("[-] 以下站点清洗失败，当前不支持手动补 raw：")
        for item in clean_failed:
            output_func(f"    - {item['site_name']}：{item.get('error', item.get('reason', 'unknown'))}")

    if not fetch_failed:
        return []

    output_func("[-] 以下站点抓取失败，可手动将 JSON 文件补到对应 raw 目录后再尝试清洗：")
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
    output_func("[*] 输入任意内容继续新增站点，直接回车退出。")
    return bool(input_func("[?] 是否继续新增站点: ").strip())


def print_summary(summaries, output_func=print):
    totals = build_totals(summaries)
    output_func(
        f"[+] 本次任务共爬取 {totals['site_count']} 个站点，{totals['model_count']} 个模型，"
        f"{totals['record_count']} 条记录。"
    )
    for summary in summaries:
        output_func(
            f"[+] {summary['site_name']}：{summary['model_count']} 个模型，"
            f"{summary['group_count']} 个分组，{summary['record_count']} 条记录"
        )
    return totals


def main(input_func=input, output_func=print):
    exit_code = 0

    while True:
        config_path = PROJECT_ROOT / DEFAULT_CONFIG_NAME
        output_func("[*] 正在读取站点配置...")
        target_config_path, sites = resolve_sites_for_run(
            config_path,
            input_func=input_func,
            output_func=output_func,
        )
        output_func(f"[*] 本次使用配置文件：{target_config_path}")

        all_summaries = []
        if not sites:
            output_func("[-] 当前没有可抓取站点。")
        else:
            output_func("[*] 开始抓取与清洗...")
            pipeline_result = run_pipeline(sites, site_root_dir=SITE_ROOT_DIR, output_func=output_func)
            recovered_summaries = handle_failed_fetch_retry(
                fetch_failed=pipeline_result["fetch_failed"],
                clean_failed=pipeline_result["clean_failed"],
                input_func=input_func,
                output_func=output_func,
                site_root_dir=SITE_ROOT_DIR,
            )
            all_summaries = pipeline_result["success_summaries"] + recovered_summaries
            if not all_summaries:
                exit_code = 1
                output_func("[-] 本次没有成功完成的站点，请检查上方错误信息。")
            print_summary(all_summaries, output_func=output_func)
            if all_summaries and wait_for_topup_plans_ready(
                SITE_ROOT_DIR / TOPUP_PLANS_NAME,
                input_func=input_func,
                output_func=output_func,
            ):
                _, _, panel_url = start_panel_server(PROJECT_ROOT, output_func=output_func)
                open_panel(panel_url, output_func=output_func)

        output_func("[*] 当前终端不会自动关闭。")
        if not ask_continue_or_exit(input_func=input_func, output_func=output_func):
            return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
