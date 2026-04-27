from pathlib import Path
import socket
import sys
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main as main_module
from main import choose_start_mode, find_available_port, validate_topup_plans_file


def test_choose_start_mode_accepts_open_panel_after_retry(monkeypatch):
    answers = iter(["x", "2"])
    messages = []

    result = choose_start_mode(input_func=lambda prompt: next(answers), output_func=messages.append)

    assert result == "panel"
    assert messages == ["[-] 请输入 1 或 2。"]


def test_main_opens_panel_without_crawling(monkeypatch):
    panel_url = "http://127.0.0.1:8000/panel/"
    opened = []
    messages = []

    monkeypatch.setattr(main_module, "choose_start_mode", lambda **kwargs: "panel")

    def fake_start_panel_server(*args, output_func=print, **kwargs):
        output_func(f"[+] 前端服务已启动：{panel_url}")
        return None, None, panel_url

    def fake_open_panel(url, output_func=print):
        opened.append(url)
        output_func(f"[+] 已尝试自动打开浏览器：{url}")

    monkeypatch.setattr(main_module, "start_panel_server", fake_start_panel_server)
    monkeypatch.setattr(main_module, "open_panel", fake_open_panel)
    monkeypatch.setattr(main_module, "ask_continue_or_exit", lambda **kwargs: False)
    monkeypatch.setattr(main_module, "run_pipeline", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("run_pipeline should not be called")))

    exit_code = main_module.main(input_func=lambda prompt: "", output_func=messages.append)

    assert exit_code == 0
    assert opened == [panel_url]
    assert messages == [
        f"[+] 前端服务已启动：{panel_url}",
        f"[+] 已尝试自动打开浏览器：{panel_url}",
        "[*] 当前终端不会自动关闭。",
    ]


def test_validate_topup_plans_file_accepts_array_root(tmp_path):
    path = tmp_path / "topup+plans.json"
    path.write_text('[{"金额": 10}]', encoding="utf-8")

    assert validate_topup_plans_file(path) is None


def test_validate_topup_plans_file_rejects_invalid_json(tmp_path):
    path = tmp_path / "topup+plans.json"
    path.write_text('{"Spark": [{"金额": 10,}]}', encoding="utf-8")

    error = validate_topup_plans_file(path)

    assert error is not None
    assert "JSON 格式错误" in error


def test_validate_topup_plans_file_rejects_object_root(tmp_path):
    path = tmp_path / "topup+plans.json"
    path.write_text('{"Spark": []}', encoding="utf-8")

    error = validate_topup_plans_file(path)

    assert error == "topup+plans.json 顶层必须是单个站点的充值/套餐数组。"


def test_main_opens_panel_without_exiting_process(monkeypatch):
    panel_url = "http://127.0.0.1:8000/panel/"
    waiting = threading.Event()

    monkeypatch.setattr(main_module, "choose_start_mode", lambda **kwargs: "panel")

    def fake_start_panel_server(*args, output_func=print, **kwargs):
        output_func(f"[+] 前端服务已启动：{panel_url}")
        return object(), object(), panel_url

    monkeypatch.setattr(main_module, "start_panel_server", fake_start_panel_server)
    monkeypatch.setattr(
        main_module,
        "open_panel",
        lambda url, output_func=print: output_func(f"[+] 已尝试自动打开浏览器：{url}"),
    )
    monkeypatch.setattr(main_module, "wait_for_panel_exit", lambda input_func=input: waiting.wait(timeout=1.0))

    result = {}

    def run_main():
        result["exit_code"] = main_module.main(input_func=lambda prompt: "", output_func=lambda _: None)

    thread = threading.Thread(target=run_main)
    thread.start()
    thread.join(timeout=0.2)

    assert thread.is_alive()

    thread.join(timeout=2)
    assert result["exit_code"] == 0


def test_start_panel_server_suppresses_http_access_logs(monkeypatch, capsys):
    server, thread, panel_url = main_module.start_panel_server(
        main_module.PROJECT_ROOT,
        output_func=lambda _: None,
    )

    handler_class = server.RequestHandlerClass.func
    handler = handler_class.__new__(handler_class)
    handler.client_address = ("127.0.0.1", 12345)
    handler.log_date_time_string = lambda: "01/Jan/2000 00:00:00"
    handler.log_message('"GET /panel/ HTTP/1.1" %s -', 200)

    server.shutdown()
    server.server_close()
    thread.join(timeout=2)

    assert panel_url.startswith("http://127.0.0.1:")
    assert capsys.readouterr().err == ""


    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    occupied_port = sock.getsockname()[1]

    try:
        port = find_available_port(preferred=occupied_port, attempts=2)
    finally:
        sock.close()

    assert port == occupied_port + 1
