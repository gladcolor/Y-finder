"""Web search through DeepSeek's Search and Fetch API (no network: requests are faked)."""
import json

import pytest

from yfinder import agent, session, websearch


class R:
    def __init__(self, status=200, body=None):
        self.status_code, self.body = status, body or {}

    def json(self):
        return self.body


@pytest.fixture()
def ds(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test0000000000000000")
    monkeypatch.delenv("YFINDER_WEB_SEARCH", raising=False)
    monkeypatch.setattr(websearch.time, "sleep", lambda s: None)
    calls = []

    def post(url, json=None, headers=None, timeout=None):
        calls.append({"url": url, "json": json, "headers": headers})
        return calls_out.pop(0) if calls_out else R(200, {"results": []})
    calls_out = []
    monkeypatch.setattr(websearch.requests, "post", post)
    return calls, calls_out


def test_provider_follows_the_key(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test0000000000000000")
    monkeypatch.delenv("YFINDER_WEB_SEARCH", raising=False)
    assert websearch.provider() == "deepseek" and [t["name"] for t in agent.tools_with_web()[-2:]] == ["web_search", "read_web_page"]
    monkeypatch.setenv("YFINDER_WEB_SEARCH", "openai")
    assert websearch.provider() == "openai" and agent.tools_with_web()[-1] == {"type": "web_search"}
    monkeypatch.setenv("YFINDER_WEB_SEARCH", "deepseek")
    monkeypatch.delenv("DEEPSEEK_API_KEY")
    monkeypatch.setattr(websearch, "ROOT", websearch.Path("/nonexistent"))
    assert websearch.provider() == "openai"  # no key anywhere: fall back instead of failing


def test_search_request_and_results(ds):
    calls, out = ds
    out.append(R(200, {"results": [
        {"title": "Street Tree Inventory Map", "url": "https://uri.yale.edu/maps/street-tree-inventory-map", "summary": "S" * 900,
         "snippet": "x", "site_name": "URI", "date": "2024-05-27T00:00:00+00:00", "authority": 3, "rerank_score": 2.1},
        {"title": "bad", "url": "javascript:alert(1)"}]}))
    res = websearch.search("New Haven street tree inventory", 99, "Which block groups need trees?")
    c = calls[0]
    assert c["url"] == websearch.SEARCH_URL and c["headers"]["Authorization"] == "Bearer sk-test0000000000000000"
    assert c["json"] == {"query": "New Haven street tree inventory lang:en", "count": 15, "ori_question": "Which block groups need trees?"}
    assert [r["url"] for r in res] == ["https://uri.yale.edu/maps/street-tree-inventory-map"]
    assert res[0]["date"] == "2024-05-27" and len(res[0]["summary"]) == 800
    websearch.search("Pakistan smog lang:ur,en", 5)
    assert calls[1]["json"]["query"] == "Pakistan smog lang:ur,en"  # the user's own language choice is kept


def test_retries_once_and_explains_errors(ds):
    calls, out = ds
    out += [R(429), R(200, {"results": [{"title": "t", "url": "https://a.example/"}]})]
    assert len(websearch.search("x", 3)) == 1 and len(calls) == 2
    out.append(R(402, {"error": {"message": "Insufficient Balance"}}))
    with pytest.raises(websearch.WebError, match="out of credit"):
        websearch.search("x", 3)
    out.append(R(404))
    with pytest.raises(websearch.WebError, match="could not be read"):
        websearch.fetch("https://a.example/page.pdf#1#1")
    assert calls[-1]["json"] == {"url": "https://a.example/page.pdf"}  # fragments dropped
    with pytest.raises(websearch.WebError):
        websearch.fetch("file:///etc/passwd")


def test_agent_tools_record_pages_and_sources(ds):
    calls, out = ds
    out.append(R(200, {"results": [{"title": "PLACES tract data", "url": "https://data.cdc.gov/places", "summary": "s"}]}))
    s = session.get(None)
    res, ev, label = agent.h_web_search(s, {"query": "CDC PLACES tract", "count": 5})
    assert label == "1 results for 'CDC PLACES tract'" and ev[0]["items"][0]["url"] == "https://data.cdc.gov/places"
    assert "https://data.cdc.gov/places" in s.citations and agent._resolve(s, "https://data.cdc.gov/places", "x")[1] == "web"
    out.append(R(200, {"title": "Page", "content": "# Page\n" + "x" * 9000}))
    page, _, _ = agent.h_read_web_page(s, {"url": "https://www.example.org/a"})
    assert page["truncated"] and len(page["content"]) == websearch.MAX_PAGE_CHARS
    cites = agent.text_citations(s, "Use [PLACES](https://data.cdc.gov/places). Not https://made-up.example/x.")
    assert cites == [{"url": "https://data.cdc.gov/places", "title": "PLACES tract data"}]  # only searched pages
    out.append(R(500))
    out.append(R(500))
    with pytest.raises(agent.ToolError, match="error 500"):
        agent.h_web_search(s, {"query": "x", "count": 3})
    session.reset(s.sid)


def test_respond_runs_the_tool_loop_for_the_handbook_writer(ds):
    calls, out = ds
    out.append(R(200, {"results": [{"title": "Docs", "url": "https://docs.example/api"}]}))

    class Obj:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    class Client:
        def __init__(self):
            self.responses, self.n, self.sent = self, 0, []

        def create(self, **kw):
            self.n += 1
            self.sent.append(kw)
            if self.n == 1:
                return Obj(id="r1", output=[Obj(type="function_call", name="web_search", call_id="c1",
                                                arguments=json.dumps({"query": "dataset api", "count": 3}))], output_text="")
            return Obj(id="r2", output=[Obj(type="message")], output_text="## What it is\nDone.")
    c = Client()
    assert websearch.respond(c, "gpt-6.1-terra", "write a handbook") == "## What it is\nDone."
    second = c.sent[1]
    assert second["previous_response_id"] == "r1" and second["input"][0]["call_id"] == "c1"
    assert "https://docs.example/api" in second["input"][0]["output"]
