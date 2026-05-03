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
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("站点 URL 不能包含用户名、密码、查询参数或片段，请将认证信息写入专用字段")
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
            item.get("ratio") if item.get("ratio") is not None else item.get("group_ratio") if item.get("group_ratio") is not None else item.get("rate_multiplier"),
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


def _build_records_from_channels(pricing_payload):
    if not isinstance(pricing_payload, dict):
        return []

    channels = pricing_payload.get("channels")
    if not isinstance(channels, list):
        return []

    records = []
    for channel in channels:
        if not isinstance(channel, dict):
            continue
        group_name = str(channel.get("name") or "").strip()
        if not group_name:
            continue
        group_description = str(channel.get("description") or "").strip()
        group_ratio = _safe_float(channel.get("rateMultiplier"))
        if group_ratio is None:
            continue
        if isinstance(group_ratio, float) and group_ratio.is_integer():
            group_ratio = int(group_ratio)

        models = channel.get("models") or []
        if not isinstance(models, list):
            continue

        for model_name in models:
            normalized_model_name = str(model_name or "").strip()
            if not normalized_model_name:
                continue
            records.append(
                _build_record(
                    model_name=normalized_model_name,
                    model_ratio=None,
                    model_price=None,
                    group_name=group_name,
                    group_description=group_description,
                    group_ratio=group_ratio,
                    quota_type=None,
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


def public_upstreams_have_available_models(payload):
    for upstream in _extract_public_upstreams(payload):
        if not isinstance(upstream, dict):
            continue
        models = upstream.get("models")
        if not isinstance(models, list):
            continue
        for model in models:
            if not isinstance(model, dict):
                continue
            if model.get("is_available") is False:
                continue
            if str(model.get("name") or "").strip():
                return True
    return False


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
    records = _build_records_from_public_upstreams(pricing_payload, user_groups_payload)
    if records:
        return records
    return _build_records_from_channels(pricing_payload)


def _extract_items_from_payload(payload):
    if payload is None:
        return []
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []

    data = payload.get("data")
    if isinstance(data, dict) and isinstance(data.get("amount_options"), list):
        discount_map = data.get("discount") or {}
        items = []
        for amount in data["amount_options"]:
            quota = _safe_float(amount)
            discount = _normalize_discount_value(_discount_map_value(discount_map, amount))
            if quota is not None and discount is not None and discount > 0:
                quota = quota / discount
            items.append({"amount": amount, "quota": quota if quota is not None else amount})
        return items

    for key in ("data", "items", "list", "plans", "packages", "products", "topups", "recharges", "options"):
        candidate = payload.get(key)
        if isinstance(candidate, list):
            return candidate
        if isinstance(candidate, dict):
            nested = _extract_items_from_payload(candidate)
            if nested:
                return nested
    return []


def _first_present(item, keys):
    for key in keys:
        if key in item and item.get(key) not in (None, ""):
            return item.get(key)
    return None


def _safe_number(value):
    number = _safe_float(value)
    if number is None:
        return None
    if number.is_integer():
        return int(number)
    return number


_MEMBER_DISCOUNT_KEYS = (
    "会员折扣",
    "member_discount",
    "memberDiscount",
    "membership_discount",
    "membershipDiscount",
    "group_discount",
    "groupDiscount",
    "permission_discount",
    "permissionDiscount",
    "benefit_discount",
    "benefitDiscount",
    "privilege_discount",
    "privilegeDiscount",
)
_DAILY_RESET_KEYS = (
    "quota_reset_period",
    "quotaResetPeriod",
    "reset_period",
    "resetPeriod",
    "reset_cycle",
    "resetCycle",
    "cycle",
    "period",
    "billing_cycle",
    "billingCycle",
)
_DAILY_RESET_VALUES = {"daily", "day", "per_day", "every_day", "each_day", "每日", "每天", "日", "按日"}
_DURATION_DAY_KEYS = (
    "duration_days",
    "durationDays",
    "valid_days",
    "validDays",
    "validity_days",
    "validityDays",
    "expire_days",
    "expireDays",
    "expired_days",
    "expiredDays",
    "period_days",
    "periodDays",
    "days",
    "day_count",
    "dayCount",
)
_TOTAL_QUOTA_KEYS = (
    "total_quota",
    "total_credit",
    "total_credits",
    "total_balance",
    "total_points",
    "quota_total",
    "credit_total",
    "credits_total",
)
_QUOTA_KEYS = ("quota", "quota_usd", "credit", "credits", "tokens", "balance", "points")
_TEXT_KEYS = ("title", "subtitle", "description", "desc", "remark")
_MEMBER_DISCOUNT_CONTEXT = (
    "会员",
    "权益",
    "权限",
    "分组",
    "模型倍率",
    "倍率",
    "调用折扣",
    "消费折扣",
    "使用折扣",
    "全站",
    "全部分组",
    "group",
    "member",
    "membership",
    "privilege",
)
_PURCHASE_DISCOUNT_CONTEXT = (
    "充值",
    "支付",
    "付款",
    "购买",
    "售价",
    "原价",
    "限时",
    "优惠",
    "首充",
    "满减",
    "折扣价",
)


def _normalize_discount_value(value):
    if isinstance(value, str):
        match = re.search(r"(\d+(?:\.\d+)?)\s*折", value)
        if match:
            value = match.group(1)
    discount = _safe_float(value)
    if discount is None or discount <= 0:
        return None
    if discount > 10:
        discount /= 100
    elif discount > 1:
        discount /= 10
    if discount <= 0:
        return None
    if discount.is_integer():
        return int(discount)
    return discount


def _normalize_member_discount_value(value):
    discount = _normalize_discount_value(value)
    if discount == 1:
        return None
    return discount


def _discount_map_value(discount_map, amount):
    if not isinstance(discount_map, dict):
        return None
    keys = [amount, str(amount)]
    number = _safe_float(amount)
    if number is not None:
        keys.append(str(int(number)) if number.is_integer() else str(number))
    for key in keys:
        if key in discount_map:
            return discount_map[key]
    return None


def _duration_days_from_item(item):
    days = _safe_float(_first_present(item, _DURATION_DAY_KEYS))
    if days is not None:
        return days

    value = _safe_float(_first_present(item, ("duration_value", "durationValue", "duration", "validity")))
    if value is None:
        return None

    unit = str(_first_present(item, ("duration_unit", "durationUnit")) or "day").strip().lower()
    if unit in {"day", "days", "d", "日", "天"}:
        return value
    if unit in {"week", "weeks", "w", "周", "星期"}:
        return value * 7
    if unit in {"month", "months", "m", "月"}:
        return value * 30
    if unit in {"year", "years", "y", "年"}:
        return value * 365
    return value


def _is_daily_reset_plan(item):
    reset_value = _first_present(item, _DAILY_RESET_KEYS)
    if reset_value is not None:
        normalized = str(reset_value).strip().lower()
        if normalized in _DAILY_RESET_VALUES:
            return True

    text = " ".join(str(item.get(key) or "") for key in _TEXT_KEYS).lower()
    return any(marker in text for marker in ("每日重置", "每天重置", "daily reset", "per day"))


def _apply_daily_multiplier(item, quota, already_total=False):
    if already_total or quota is None or not _is_daily_reset_plan(item):
        return quota
    duration_days = _duration_days_from_item(item)
    if duration_days is None:
        return quota
    return quota * duration_days


def _quota_from_item(item, item_type):
    documented_quota = _safe_float(_first_present(item, ("额度",)))
    if documented_quota is not None:
        if item_type == "充值":
            recharge_discount = _normalize_discount_value(_first_present(item, ("discount", "rate", "rebate")))
            if recharge_discount is not None and recharge_discount > 0:
                documented_quota = documented_quota / recharge_discount
        return _safe_number(documented_quota)

    total_quota = _safe_float(_first_present(item, _TOTAL_QUOTA_KEYS))
    if total_quota is not None:
        return _safe_number(total_quota)

    total_amount = _safe_float(item.get("total_amount"))
    if total_amount is not None:
        quota = total_amount / 500000
        if item_type == "套餐":
            quota = _apply_daily_multiplier(item, quota)
        return _safe_number(quota)

    quota = _safe_float(_first_present(item, _QUOTA_KEYS))
    if quota is not None:
        recharge_discount = _normalize_discount_value(_first_present(item, ("discount", "rate", "rebate")))
        if item_type == "充值" and recharge_discount is not None and recharge_discount > 0:
            quota = quota / recharge_discount
        if item_type == "套餐":
            quota = _apply_daily_multiplier(item, quota)
        return _safe_number(quota)

    if item_type == "充值":
        amount = _safe_float(_first_present(item, ("金额", "amount", "price", "money", "price_amount", "pay_amount", "payment", "cost")))
        recharge_discount = _normalize_discount_value(_first_present(item, ("discount", "rate", "rebate")))
        if amount is not None and recharge_discount is not None and recharge_discount > 0:
            return _safe_number(amount / recharge_discount)

    return None


def _discount_has_member_context(text):
    lowered = text.lower()
    if not any(keyword in lowered for keyword in _MEMBER_DISCOUNT_CONTEXT):
        return False
    if any(keyword in lowered for keyword in ("分组", "权限", "模型倍率", "倍率", "调用折扣", "消费折扣", "使用折扣", "全站", "全部分组", "group", "privilege")):
        return True
    return not any(keyword in lowered for keyword in _PURCHASE_DISCOUNT_CONTEXT)


def _extract_member_discount_from_text(*values):
    for value in values:
        if not value:
            continue
        text = str(value)
        match = re.search(r"(\d+(?:\.\d+)?)\s*折", text)
        if match and _discount_has_member_context(text):
            return _normalize_member_discount_value(match.group(0))
    return None


def _extract_discount_from_text(*values):
    return _extract_member_discount_from_text(*values)


def _quota_from_plan_item(item):
    return _quota_from_item(item, "套餐")


def _normalize_topup_plan_item(item, item_type):
    if not isinstance(item, dict):
        return None
    if isinstance(item.get("plan"), dict):
        item = item["plan"]

    amount = _safe_number(
        _first_present(item, ("金额", "amount", "price", "money", "price_amount", "pay_amount", "payment", "cost"))
    )
    quota = _quota_from_item(item, item_type)
    discount = _normalize_member_discount_value(_first_present(item, _MEMBER_DISCOUNT_KEYS))
    if discount is None and item_type == "套餐":
        discount = _extract_member_discount_from_text(
            item.get("title"),
            item.get("subtitle"),
            item.get("description"),
            item.get("desc"),
            item.get("remark"),
        )
    if amount is None or quota is None or amount <= 0 or quota <= 0:
        return None

    plan_name = _first_present(item, ("套餐名", "display_name", "name", "title", "plan_name", "package_name", "product_name"))
    if plan_name is not None:
        plan_name = str(plan_name).strip() or None

    return {
        "类型": item_type,
        "套餐名": plan_name if item_type == "套餐" else None,
        "金额": amount,
        "额度": quota,
        "会员折扣": discount,
    }


def _topup_effective_rate_key(record):
    amount = _safe_float(record.get("金额"))
    quota = _safe_float(record.get("额度"))
    if amount is None or quota is None or amount <= 0 or quota <= 0:
        return None
    return round(amount / quota, 10)


def _dedupe_topup_records(records):
    selected_by_rate = {}
    fallback_records = []
    for index, record in enumerate(records):
        rate_key = _topup_effective_rate_key(record)
        if rate_key is None:
            fallback_records.append((index, record))
            continue

        amount = _safe_float(record.get("金额"))
        selected = selected_by_rate.get(rate_key)
        if selected is None or amount < selected[1]:
            selected_by_rate[rate_key] = (index, amount, record)

    selected_indexes = {selected[0] for selected in selected_by_rate.values()}
    fallback_seen = set()
    deduped = []
    for index, record in enumerate(records):
        if index in selected_indexes:
            deduped.append(record)
            continue
        if not any(index == fallback_index for fallback_index, _ in fallback_records):
            continue
        key = (
            record.get("金额"),
            record.get("额度"),
            record.get("会员折扣"),
        )
        if key in fallback_seen:
            continue
        fallback_seen.add(key)
        deduped.append(record)
    return deduped


def build_topup_plan_records(topup_payload=None, plans_payload=None):
    topup_records = []
    for item in _extract_items_from_payload(topup_payload):
        record = _normalize_topup_plan_item(item, "充值")
        if record is not None:
            topup_records.append(record)

    records = _dedupe_topup_records(topup_records)
    for item in _extract_items_from_payload(plans_payload):
        record = _normalize_topup_plan_item(item, "套餐")
        if record is not None:
            records.append(record)
    return records


def parse_pricing_payload(payload):
    return build_site_records(payload)
