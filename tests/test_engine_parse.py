import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from jh.engine import parse_stream_line, StreamState, tool_label  # noqa: E402


def j(o):
    return json.dumps(o)


def tool_use(name, inp, tid="t1"):
    return j({"type": "assistant", "message": {"content": [{"type": "tool_use", "id": tid, "name": name, "input": inp}]}})


def tool_result(tid, text, is_error=False):
    return j({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": tid,
                                                         "content": [{"type": "text", "text": text}], "is_error": is_error}]}})


class ParseStreamLineTest(unittest.TestCase):
    def test_init_connected(self):
        st = StreamState()
        line = j({"type": "system", "subtype": "init", "model": "claude-x", "session_id": "s",
                  "tools": ["mcp__jobhunt__log_step", "mcp__claude-in-chrome__navigate"],
                  "mcp_servers": [{"name": "jobhunt", "status": "connected"}, {"name": "claude-in-chrome", "status": "connected"}]})
        ev = parse_stream_line(line, st)
        self.assertEqual(len(ev), 1)
        typ, msg, lvl, data = ev[0]
        self.assertEqual(typ, "step")
        self.assertIn("jobhunt connected", msg)
        self.assertIn("Chrome connected", msg)
        self.assertEqual(data["model"], "claude-x")
        self.assertIsNotNone(st.init)

    def test_init_mcp_failed_warns(self):
        line = j({"type": "system", "subtype": "init", "tools": [],
                  "mcp_servers": [{"name": "jobhunt", "status": "failed"}]})
        ev = parse_stream_line(line)
        types = [e[0] for e in ev]
        self.assertEqual(types.count("warning"), 2)  # jobhunt failed + no chrome
        self.assertTrue(any("failed" in e[1] for e in ev))

    def test_assistant_text_trimmed(self):
        long = "word " * 200
        ev = parse_stream_line(j({"type": "assistant", "message": {"content": [{"type": "text", "text": long}]}}))
        self.assertEqual(ev[0][0], "step")
        self.assertLessEqual(len(ev[0][1]), 300)
        self.assertEqual(ev[0][2], "info")

    def test_empty_text_and_thinking_skipped(self):
        ev = parse_stream_line(j({"type": "assistant", "message": {"content": [
            {"type": "text", "text": "   "}, {"type": "thinking", "thinking": "hmm"}]}}))
        self.assertEqual(ev, [])

    def test_chrome_navigate_label(self):
        ev = parse_stream_line(tool_use("mcp__claude-in-chrome__navigate",
                                        {"url": "https://www.linkedin.com/jobs/search/?keywords=salesforce&geoId=1", "tabId": 3}))
        typ, msg, lvl, data = ev[0]
        self.assertEqual(typ, "tool")
        self.assertEqual(msg, "Opening linkedin.com/jobs/search")
        self.assertNotIn("geoId", msg)
        self.assertEqual(data["tool"], "mcp__claude-in-chrome__navigate")

    def test_chrome_other_labels(self):
        cases = {
            ("javascript_tool", "{}"): "Running page script",
            ("get_page_text", "{}"): "Reading page",
            ("computer", '{"action":"screenshot"}'): "Looking at page",
            ("computer", '{"action":"left_click"}'): "Clicking",
            ("tabs_create_mcp", "{}"): "Opening my own tab",
            ("file_upload", "{}"): "Uploading resume",
        }
        for (tool, inp), want in cases.items():
            self.assertEqual(tool_label("mcp__claude-in-chrome__" + tool, json.loads(inp)), want)
        self.assertTrue(tool_label("mcp__claude-in-chrome__find", {"query": "email"}).startswith("Finding element"))

    def test_jobhunt_labels(self):
        ev = parse_stream_line(tool_use("mcp__jobhunt__save_job", {"company": "Capio Group", "role": "Salesforce Developer",
                                                                   "decision": "qualified"}))
        self.assertEqual(ev[0][1], "Saving job: Capio Group — Salesforce Developer")
        # log_step is narrated by the MCP server itself -> no duplicate tool event
        self.assertEqual(parse_stream_line(tool_use("mcp__jobhunt__log_step", {"message": "hi"})), [])

    def test_send_application_uses_contact_name(self):
        st = StreamState()
        parse_stream_line(tool_use("mcp__jobhunt__save_contact", {"name": "Jane Doe", "title": "Recruiter", "job_id": 1}, "a"), st)
        parse_stream_line(tool_result("a", json.dumps({"contact_id": 7})), st)
        ev = parse_stream_line(tool_use("mcp__jobhunt__send_application", {"contact_id": 7, "job_id": 1, "subject": "x"}, "b"), st)
        self.assertEqual(ev[0][1], "Sending application to Jane Doe")
        parse_stream_line(tool_result("b", json.dumps({"ok": True, "email_id": 3})), st)
        self.assertEqual(st.counts["sent"], 1)
        self.assertEqual(st.counts["contacts"], 1)

    def test_counts_from_save_job_results(self):
        st = StreamState()
        parse_stream_line(tool_use("mcp__jobhunt__save_job", {"company": "A", "role": "R", "decision": "skipped"}, "x"), st)
        parse_stream_line(tool_result("x", json.dumps({"job_id": 1})), st)
        parse_stream_line(tool_use("mcp__jobhunt__save_job", {"company": "B", "role": "R", "decision": "qualified"}, "y"), st)
        parse_stream_line(tool_result("y", json.dumps({"job_id": 2})), st)
        self.assertEqual((st.counts["found"], st.counts["skipped"], st.counts["qualified"]), (2, 1, 1))

    def test_tool_error_result_warns(self):
        st = StreamState()
        parse_stream_line(tool_use("mcp__claude-in-chrome__navigate", {"url": "x.com"}, "z"), st)
        ev = parse_stream_line(tool_result("z", "Tab not found", is_error=True), st)
        self.assertEqual(ev[0][0], "warning")
        self.assertEqual(ev[0][2], "warn")
        self.assertIn("navigate failed", ev[0][1])
        self.assertIn("mcp__claude-in-chrome__navigate", st.tool_err)

    def test_successful_tool_result_is_silent(self):
        self.assertEqual(parse_stream_line(tool_result("q", "ok")), [])

    def test_result_success(self):
        ev = parse_stream_line(j({"type": "result", "subtype": "success", "is_error": False, "total_cost_usd": 0.8234,
                                  "num_turns": 34, "result": "Sent 3 emails"}))
        typ, msg, lvl, data = ev[0]
        self.assertEqual(typ, "lane_finished")
        self.assertEqual(lvl, "success")
        self.assertEqual(data["status"], "done")
        self.assertEqual(data["cost_usd"], 0.8234)
        self.assertEqual(data["turns"], 34)
        self.assertIn("$0.82", msg)

    def test_result_error(self):
        ev = parse_stream_line(j({"type": "result", "subtype": "error_during_execution", "is_error": True,
                                  "total_cost_usd": 0.1, "num_turns": 2}))
        self.assertEqual(ev[0][2], "error")
        self.assertEqual(ev[0][3]["status"], "error")

    def test_result_max_turns_is_done_with_warning(self):
        ev = parse_stream_line(j({"type": "result", "subtype": "error_max_turns", "is_error": True, "num_turns": 150}))
        self.assertEqual(ev[0][3]["status"], "done")
        self.assertEqual(ev[0][2], "warn")

    def test_junk_and_ignored_lines(self):
        ev = parse_stream_line("Warning: something not json")
        self.assertEqual(ev[0][2], "debug")
        self.assertEqual(parse_stream_line(""), [])
        self.assertEqual(parse_stream_line("   \n"), [])
        self.assertEqual(parse_stream_line(j({"type": "stream_event", "event": {"type": "content_block_delta"}})), [])
        self.assertEqual(parse_stream_line(j({"type": "rate_limit_event"})), [])
        self.assertEqual(parse_stream_line(j([1, 2])), [])
        self.assertEqual(parse_stream_line(j({"type": "system", "subtype": "status"})), [])


if __name__ == "__main__":
    unittest.main()
