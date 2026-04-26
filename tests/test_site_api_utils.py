from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "script"))

from site_api_utils import build_site_records


def test_build_site_records_expands_channels_payload_into_records():
    pricing_payload = {
        "channels": [
            {
                "name": "max（折扣分组）",
                "rateMultiplier": 1,
                "description": "满血官方正版模型。",
                "models": ["claude-opus-4-6", "claude-sonnet-4-6"],
            },
            {
                "name": "gemini-ultra",
                "rateMultiplier": 0.6,
                "description": "满血 gemini，性价比高",
                "models": ["gemini-3-flash"],
            },
        ]
    }

    records = build_site_records(pricing_payload)

    assert records == [
        {
            "model_name": "claude-opus-4-6",
            "model_ratio": None,
            "model_price": None,
            "group_name": "max（折扣分组）",
            "group_description": "满血官方正版模型。",
            "group_ratio": 1,
            "quota_type": "按量计费",
        },
        {
            "model_name": "claude-sonnet-4-6",
            "model_ratio": None,
            "model_price": None,
            "group_name": "max（折扣分组）",
            "group_description": "满血官方正版模型。",
            "group_ratio": 1,
            "quota_type": "按量计费",
        },
        {
            "model_name": "gemini-3-flash",
            "model_ratio": None,
            "model_price": None,
            "group_name": "gemini-ultra",
            "group_description": "满血 gemini，性价比高",
            "group_ratio": 0.6,
            "quota_type": "按量计费",
        },
    ]


def test_build_site_records_skips_invalid_channels_entries():
    pricing_payload = {
        "channels": [
            {"name": "", "rateMultiplier": 1, "description": "ignored", "models": ["claude-opus-4-6"]},
            {"name": "missing-ratio", "description": "ignored", "models": ["claude-sonnet-4-6"]},
            {"name": "bad-models", "rateMultiplier": 2, "description": "ignored", "models": "claude-opus-4-6"},
            {"name": "mixed", "rateMultiplier": 1.5, "description": "kept", "models": ["", None, "claude-haiku-4-5-20251001"]},
        ]
    }

    records = build_site_records(pricing_payload)

    assert records == [
        {
            "model_name": "claude-haiku-4-5-20251001",
            "model_ratio": None,
            "model_price": None,
            "group_name": "mixed",
            "group_description": "kept",
            "group_ratio": 1.5,
            "quota_type": "按量计费",
        }
    ]


def test_build_site_records_prefers_upstreams_when_both_structures_exist():
    pricing_payload = {
        "channels": [
            {
                "name": "channel-group",
                "rateMultiplier": 1,
                "description": "from channels",
                "models": ["claude-opus-4-6"],
            }
        ],
        "upstreams": [
            {
                "name": "public-group",
                "rate": 2,
                "remark": "from upstreams",
                "models": [
                    {
                        "name": "claude-opus-4-6",
                        "input_price": 0.5,
                        "request_price": 10,
                        "billing_mode": "request",
                    }
                ],
            }
        ],
    }

    records = build_site_records(pricing_payload)

    assert records == [
        {
            "model_name": "claude-opus-4-6",
            "model_ratio": 0.5,
            "model_price": 10,
            "group_name": "public-group",
            "group_description": "from upstreams",
            "group_ratio": 2,
            "quota_type": "按次计费",
        }
    ]
