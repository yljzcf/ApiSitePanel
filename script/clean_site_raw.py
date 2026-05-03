from pathlib import Path

from site_api_utils import build_site_records, build_topup_plan_records, load_json, sanitize_path_component, save_json


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
SITE_ROOT_DIR = PROJECT_ROOT / "site"
TOPUP_PLANS_NAME = "topup+plans.json"


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


def clean_topup_plans_for_site(site_dir, raw_dir):
    site_dir = Path(site_dir)
    raw_dir = Path(raw_dir)
    topup_path = raw_dir / "topup.json"
    plans_path = raw_dir / "plans.json"
    output_path = site_dir / TOPUP_PLANS_NAME

    existing_records = load_json(output_path) if output_path.exists() else None

    topup_payload = load_json(topup_path) if topup_path.exists() else None
    plans_payload = load_json(plans_path) if plans_path.exists() else None
    records = build_topup_plan_records(topup_payload, plans_payload)
    if records:
        save_json(output_path, records)
        return output_path, len(records), True
    if existing_records:
        return output_path, len(existing_records), False
    if existing_records is not None:
        return output_path, 0, False
    save_json(output_path, [])
    return output_path, 0, False


def clean_one_site(site_dir, raw_dir, output_name=None):
    site_dir = Path(site_dir)
    raw_dir = Path(raw_dir)
    pricing_path = raw_dir / "pricing.json"
    output_stem = sanitize_path_component(str(output_name or site_dir.name).strip() or site_dir.name, fallback=site_dir.name)
    output_path = site_dir / f"{output_stem}.json"
    if not pricing_path.exists():
        if output_path.exists():
            records = load_json(output_path)
            topup_plans_path, topup_plan_count, topup_plans_auto_generated = clean_topup_plans_for_site(site_dir, raw_dir)
            model_names = {record.get("model_name") for record in records if record.get("model_name")}
            group_names = {record.get("group_name") for record in records if record.get("group_name")}
            return output_path, {
                "site_name": site_dir.name,
                "output_file": output_path.name,
                "model_count": len(model_names),
                "group_count": len(group_names),
                "record_count": len(records),
                "topup_plan_count": topup_plan_count,
                "topup_plans_existing_count": topup_plan_count if not topup_plans_auto_generated else 0,
                "topup_plans_auto_generated": topup_plans_auto_generated,
                "topup_plans_file": topup_plans_path.name,
                "preserved_existing_output": True,
            }
        raise ValueError(f"缺少文件: {pricing_path}")

    pricing_payload = load_json(pricing_path)
    user_groups_path = raw_dir / "user_groups.json"
    user_groups_payload = load_json(user_groups_path) if user_groups_path.exists() else {}
    records = build_site_records(pricing_payload, user_groups_payload)
    preserved_existing_output = False
    if records:
        save_json(output_path, records)
    elif output_path.exists():
        records = load_json(output_path)
        preserved_existing_output = True
    else:
        raise ValueError("未能从 pricing 生成有效记录，且没有可沿用的旧输出")
    topup_plans_path, topup_plan_count, topup_plans_auto_generated = clean_topup_plans_for_site(site_dir, raw_dir)

    model_names = {record.get("model_name") for record in records if record.get("model_name")}
    group_names = {record.get("group_name") for record in records if record.get("group_name")}
    summary = {
        "site_name": site_dir.name,
        "output_file": output_path.name,
        "model_count": len(model_names),
        "group_count": len(group_names),
        "record_count": len(records),
        "topup_plan_count": topup_plan_count,
        "topup_plans_existing_count": topup_plan_count if not topup_plans_auto_generated else 0,
        "topup_plans_auto_generated": topup_plans_auto_generated,
        "topup_plans_file": topup_plans_path.name,
        "preserved_existing_output": preserved_existing_output,
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
