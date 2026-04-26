import json
import re
from pathlib import Path
from urllib.parse import urlparse


_RATIO_PATTERN = re.compile(r"x\s*(\d+(?:\.\d+)?)", re.IGNORECASE)
_ANOMALOUS_RATIOS = {999, 5201314}
_QUOTA_TYPE_MAP = {
    0: "按量计费",
    1: "按次计费",
    "0": "按量计费",
    "1": "按次计费",
    "token": "按量计费",
    "request": "按次计费",
    "按量计费": "按量计费",
    "按次计费": "按次计费",
}


def normalize_base_url(base_url):
    normalized = str(base_url or "").strip()
    if not normalized:
        raise ValueError("站点 URL 不能为空")
    if normalized.endswith("/"):
        normalized = normalized[:-1]
    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("站点 URL 必须包含 http/https 协议和有效域名")
    return normalized


def sanitize_path_component(value, fallback="site"):
    normalized = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", str(value or "").strip())
    normalized = normalized.rstrip(". ")
    normalized = re.sub(r"_+", "_", normalized)
    return normalized or fallback


def site_dir_name_from_url(base_url, site_name=None):
    normalized_site_name = str(site_name or "").strip()
    if normalized_site_name:
        return sanitize_path_component(normalized_site_name, fallback="site")
    parsed = urlparse(normalize_base_url(base_url))
    return sanitize_path_component(parsed.netloc.replace(":", "_"), fallback="site")


def save_json(path, data):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def extract_ratio_from_description(description):
    if not description:
        return None

    match = _RATIO_PATTERN.search(str(description))
    if not match:
        return None
    return float(match.group(1))


def _safe_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_group_name(raw_name):
    return str(raw_name or "").strip()


def _resolve_group_ratio(raw_ratio, description):
    extracted_ratio = extract_ratio_from_description(description)

    if raw_ratio in _ANOMALOUS_RATIOS:
        if extracted_ratio is not None:
            return extracted_ratio
        return raw_ratio
    if raw_ratio is not None:
        return raw_ratio
    return extracted_ratio


def _normalize_group_entry(name, description, raw_ratio, allow_missing_ratio=False):
    group_name = _normalize_group_name(name)
    if not group_name:
        return None

    resolved_raw_ratio = _safe_float(raw_ratio)
    ratio = _resolve_group_ratio(resolved_raw_ratio, description)
    if ratio is None and not allow_missing_ratio:
        return None

    if isinstance(ratio, float) and ratio.is_integer():
        ratio = int(ratio)

    return {
        "group_name": group_name,
        "group_description": str(description or "").strip(),
        "group_ratio": ratio,
    }


def _normalize_quota_type(value, default="按量计费"):
    if value is None:
        return default
    return _QUOTA_TYPE_MAP.get(value, default)


def _build_record(model_name, model_ratio, model_price, group_name, group_description, group_ratio, quota_type):
    return {
        "model_name": model_name,
        "model_ratio": model_ratio,
        "model_price": model_price,
        "group_name": group_name,
        "group_description": group_description,
        "group_ratio": group_ratio,
        "quota_type": _normalize_quota_type(quota_type),
    }


def parse_user_groups_payload(payload):
    if isinstance(payload, dict):
        groups_data = payload.get("data")
        if isinstance(groups_data, dict):
            groups_data = groups_data.get("groups") or groups_data.get("items") or groups_data
        elif groups_data is None:
            groups_data = payload.get("groups") or payload.get("items") or payload
    else:
        groups_data = payload

    if isinstance(groups_data, dict):
        iterable = groups_data.values()
    elif isinstance(groups_data, list):
        iterable = groups_data
    else:
        return {}

    group_map = {}
    for item in iterable:
        if not isinstance(item, dict):
            continue
        entry = _normalize_group_entry(
            item.get("group") or item.get("group_name") or item.get("name") or item.get("key"),
            item.get("description") or item.get("desc") or item.get("remark") or item.get("label"),
            item.get("ratio") if item.get("ratio") is not None else item.get("group_ratio"),
            allow_missing_ratio=True,
        )
        if entry is None:
            continue
        group_map[entry["group_name"]] = entry
    return group_map


def merge_group_maps(*group_maps):
    merged = {}
    for group_map in group_maps:
        if not isinstance(group_map, dict):
            continue
        for group_name, group in group_map.items():
            if not isinstance(group, dict):
                continue
            existing = merged.get(
                group_name,
                {
                    "group_name": group_name,
                    "group_description": "",
                    "group_ratio": None,
                },
            )
            merged[group_name] = {
                "group_name": group_name,
                "group_description": group.get("group_description") or existing["group_description"],
                "group_ratio": group.get("group_ratio") if group.get("group_ratio") is not None else existing["group_ratio"],
            }
    return merged


def _build_group_map(payload):
    nested_data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    group_ratio_map = payload.get("group_ratio") or {}
    usable_groups = payload.get("usable_group") or nested_data.get("groups") or []
    groups = {}

    if isinstance(usable_groups, dict):
        iterable = []
        for name, description in usable_groups.items():
            iterable.append({"name": name, "description": description})
    else:
        iterable = usable_groups

    for group in iterable:
        if not isinstance(group, dict):
            continue
        name = group.get("name") or group.get("group_name")
        if not name:
            continue

        description = group.get("description") or group.get("desc") or group.get("remark") or group.get("label") or ""
        raw_ratio = group_ratio_map.get(name, group.get("ratio"))
        entry = _normalize_group_entry(name, description, raw_ratio)
        if entry is None:
            continue
        groups[name] = entry

    return groups


def build_expanded_records(models, group_map):
    records = []
    for model in models or []:
        if not isinstance(model, dict):
            continue
        for group_name in model.get("enable_groups") or []:
            group = group_map.get(group_name)
            if not group or group.get("group_ratio") is None:
                continue
            records.append(
                _build_record(
                    model_name=model.get("model_name"),
                    model_ratio=model.get("model_ratio"),
                    model_price=model.get("model_price"),
                    group_name=group.get("group_name"),
                    group_description=group.get("group_description"),
                    group_ratio=group.get("group_ratio"),
                    quota_type=model.get("quota_type"),
                )
            )
    return records


def _extract_models(payload):
    nested_data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    return payload.get("data") if isinstance(payload.get("data"), list) else nested_data.get("models") or []


def _build_records_from_structured_groups_models(pricing_payload):
    if not isinstance(pricing_payload, dict):
        return []

    groups = pricing_payload.get("groups")
    models = pricing_payload.get("models")
    if not isinstance(groups, list) or not isinstance(models, list):
        return []

    group_map = {}
    for group in groups:
        if not isinstance(group, dict):
            continue
        group_id = str(group.get("id") or "").strip()
        group_name = str(group.get("name") or "").strip()
        if not group_id or not group_name:
            continue
        group_map[group_id] = {
            "group_name": group_name,
            "group_description": str(group.get("description") or group.get("remark") or "").strip(),
            "group_ratio": _safe_float(group.get("rate_multiplier")),
            "model_price": group.get("per_request_price"),
        }

    records = []
    for model in models:
        if not isinstance(model, dict):
            continue
        if model.get("available") is not True:
            continue

        model_name = str(model.get("id") or "").strip()
        if not model_name:
            continue

        input_price = _safe_float(model.get("input_price"))
        model_ratio = input_price / 2 if input_price is not None else None
        group_ids = model.get("group_ids") or []
        if not isinstance(group_ids, list):
            continue

        for group_id in group_ids:
            group = group_map.get(str(group_id or "").strip())
            if not group or group.get("group_ratio") is None:
                continue

            model_price = group.get("model_price")
            quota_type = "按量计费" if model_price is None else "按次计费"
            records.append(
                _build_record(
                    model_name=model_name,
                    model_ratio=model_ratio,
                    model_price=model_price,
                    group_name=group.get("group_name"),
                    group_description=group.get("group_description"),
                    group_ratio=group.get("group_ratio"),
                    quota_type=quota_type,
                )
            )

    return records


def _build_records_from_model_group(pricing_payload):
    data = pricing_payload.get("data") if isinstance(pricing_payload, dict) else None
    if not isinstance(data, dict):
        return []

    model_group = data.get("model_group")
    if not isinstance(model_group, dict):
        return []

    records = []
    for group_name, group_info in model_group.items():
        if not isinstance(group_info, dict):
            continue
        group_description = str(group_info.get("DisplayName") or "").strip()
        group_ratio = _safe_float(group_info.get("GroupRatio"))
        if group_ratio is None:
            continue
        if isinstance(group_ratio, float) and group_ratio.is_integer():
            group_ratio = int(group_ratio)

        model_price_map = group_info.get("ModelPrice")
        if not isinstance(model_price_map, dict):
            continue

        for model_name, model_info in model_price_map.items():
            if not isinstance(model_info, dict):
                continue
            records.append(
                _build_record(
                    model_name=model_name,
                    model_ratio=None,
                    model_price=model_info.get("price"),
                    group_name=str(group_name).strip(),
                    group_description=group_description,
                    group_ratio=group_ratio,
                    quota_type=model_info.get("priceType"),
                )
            )
    return records


def _extract_public_upstreams(payload):
    if not isinstance(payload, dict):
        return []
    upstreams = payload.get("upstreams")
    if not isinstance(upstreams, list):
        return []
    return upstreams


def _build_records_from_public_upstreams(pricing_payload, user_groups_payload=None):
    pricing_upstreams = _extract_public_upstreams(pricing_payload)
    if not pricing_upstreams:
        return []

    user_upstreams = _extract_public_upstreams(user_groups_payload or {})
    user_upstream_map = {}
    for upstream in user_upstreams:
        if not isinstance(upstream, dict):
            continue
        upstream_name = str(upstream.get("name") or "").strip()
        upstream_prefix = str(upstream.get("prefix") or "").strip()
        if upstream_name:
            user_upstream_map[upstream_name] = upstream
        if upstream_prefix:
            user_upstream_map[upstream_prefix] = upstream

    records = []
    for upstream in pricing_upstreams:
        if not isinstance(upstream, dict):
            continue
        upstream_name = str(upstream.get("name") or "").strip()
        upstream_prefix = str(upstream.get("prefix") or "").strip()
        upstream_meta = user_upstream_map.get(upstream_name) or user_upstream_map.get(upstream_prefix) or upstream
        group_name = str(upstream_meta.get("name") or upstream_name).strip()
        if not group_name:
            continue
        group_description = str(upstream_meta.get("remark") or upstream.get("remark") or "").strip()
        group_ratio = _safe_float(upstream_meta.get("rate"))
        if group_ratio is None:
            continue
        if isinstance(group_ratio, float) and group_ratio.is_integer():
            group_ratio = int(group_ratio)
        models = upstream.get("models") or []
        if not isinstance(models, list):
            continue
        for model in models:
            if not isinstance(model, dict):
                continue
            if model.get("is_available") is False:
                continue
            model_name = str(model.get("name") or "").strip()
            if not model_name:
                continue
            records.append(
                _build_record(
                    model_name=model_name,
                    model_ratio=model.get("input_price"),
                    model_price=model.get("request_price"),
                    group_name=group_name,
                    group_description=group_description,
                    group_ratio=group_ratio,
                    quota_type=model.get("billing_mode"),
                )
            )
    return records


def build_site_records(pricing_payload, user_groups_payload=None):
    pricing_group_map = _build_group_map(pricing_payload)
    user_group_map = parse_user_groups_payload(user_groups_payload or {})
    merged_group_map = merge_group_maps(pricing_group_map, user_group_map)
    records = build_expanded_records(_extract_models(pricing_payload), merged_group_map)
    if records:
        return records
    records = _build_records_from_structured_groups_models(pricing_payload)
    if records:
        return records
    records = _build_records_from_model_group(pricing_payload)
    if records:
        return records
    return _build_records_from_public_upstreams(pricing_payload, user_groups_payload)


def parse_pricing_payload(payload):
    return build_site_records(payload)
