from pathlib import Path

from site_api_utils import build_site_records, load_json, sanitize_path_component, save_json


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
SITE_ROOT_DIR = PROJECT_ROOT / "site"


def iter_site_raw_dirs(site_root_dir=SITE_ROOT_DIR):
    site_root_dir = Path(site_root_dir)
    if not site_root_dir.is_dir():
        return
    for child in site_root_dir.iterdir():
        if not child.is_dir():
            continue
        raw_dir = child / "raw"
        if raw_dir.is_dir():
            yield child, raw_dir


def clean_one_site(site_dir, raw_dir, output_name=None):
    site_dir = Path(site_dir)
    raw_dir = Path(raw_dir)
    pricing_path = raw_dir / "pricing.json"
    if not pricing_path.exists():
        raise ValueError(f"缺少文件: {pricing_path}")

    pricing_payload = load_json(pricing_path)
    user_groups_path = raw_dir / "user_groups.json"
    user_groups_payload = load_json(user_groups_path) if user_groups_path.exists() else {}
    records = build_site_records(pricing_payload, user_groups_payload)
    output_stem = sanitize_path_component(str(output_name or site_dir.name).strip() or site_dir.name, fallback=site_dir.name)
    output_path = site_dir / f"{output_stem}.json"
    save_json(output_path, records)

    model_names = {record.get("model_name") for record in records if record.get("model_name")}
    group_names = {record.get("group_name") for record in records if record.get("group_name")}
    summary = {
        "site_name": site_dir.name,
        "output_file": output_path.name,
        "model_count": len(model_names),
        "group_count": len(group_names),
        "record_count": len(records),
    }
    return output_path, summary


def clean_all_sites(site_root_dir=SITE_ROOT_DIR):
    summaries = []
    for site_dir, raw_dir in iter_site_raw_dirs(site_root_dir):
        try:
            output_path, summary = clean_one_site(site_dir, raw_dir)
            summaries.append(summary)
            print(f"[+] {site_dir.name}: 已生成 {output_path.name}")
        except Exception as exc:
            print(f"[-] {site_dir.name}: 清洗失败: {exc}")
    return summaries


def main():
    summaries = clean_all_sites(SITE_ROOT_DIR)
    if not summaries:
        print("[-] 未发现可处理的 raw 目录")
        return 1

    print(f"[+] 本次共处理了 {len(summaries)} 个站点")
    for summary in summaries:
        print(
            f"[+] {summary['site_name']}：{summary['model_count']} 个模型，"
            f"{summary['group_count']} 个分组，展开后共 {summary['record_count']} 项记录"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
