from pathlib import Path
import socket
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from main import find_available_port, validate_topup_plans_file


def test_validate_topup_plans_file_accepts_object(tmp_path):
    path = tmp_path / "topup+plans.json"
    path.write_text('{"Spark": []}', encoding="utf-8")

    assert validate_topup_plans_file(path) is None


def test_validate_topup_plans_file_rejects_invalid_json(tmp_path):
    path = tmp_path / "topup+plans.json"
    path.write_text('{"Spark": [{"金额": 10,}]}', encoding="utf-8")

    error = validate_topup_plans_file(path)

    assert error is not None
    assert "JSON 格式错误" in error


def test_validate_topup_plans_file_rejects_non_object_root(tmp_path):
    path = tmp_path / "topup+plans.json"
    path.write_text('[]', encoding="utf-8")

    error = validate_topup_plans_file(path)

    assert error == "topup+plans.json 顶层必须是按站点名分组的对象。"


def test_find_available_port_skips_occupied_port():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    occupied_port = sock.getsockname()[1]

    try:
        port = find_available_port(preferred=occupied_port, attempts=2)
    finally:
        sock.close()

    assert port == occupied_port + 1
