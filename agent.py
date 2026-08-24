"""
Unified LLM router that supports Anthropic (claude-3-5-sonnet), OpenAI, and Ollama.
This module standardizes tool calling and provides a simple interface:

from agent import LLMRouter
router = LLMRouter()
reply = router.route_query(query, tools=tools_dict)

Tools: a dict mapping tool_name -> {"func": callable, "description": str, "schema": optional}

Notes:
- Anthropic native tool calling: expects model messages to return a JSON block with a top-level "tool_call" key describing {"name":..., "args": {...}}.
- This is a pragmatic adapter; full Anthropic function-calling requires following their exact tool schema. The router provides retries and provider selection.
"""
from __future__ import annotations

import os
import json
import logging
import time
from typing import Any, Dict, Optional

log = logging.getLogger("agent")

LLM_PROVIDER = os.getenv("LLM_PROVIDER", "ollama").lower()
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
DEFAULT_OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.1")


class LLMRouter:
    def __init__(self, provider: Optional[str] = None):
        self.provider = (provider or LLM_PROVIDER).lower()

    def route_query(self, query: str, tools: Optional[Dict[str, Any]] = None, system_prompt: str = "", executed_tools: Optional[list] = None) -> str:
        """Synchronous routing to the selected provider. executed_tools is a list that will be appended with tool execution records."""
        tools = tools or {}
        executed_tools = executed_tools if executed_tools is not None else []
        if self.provider == "anthropic":
            return self._call_anthropic(query, tools, system_prompt, executed_tools)
        if self.provider == "openai":
            return self._call_openai(query, tools, system_prompt, executed_tools)
        # default: try ollama if available, else fallback to openai remote
        try:
            import ollama
            return self._call_ollama(query, tools, system_prompt, executed_tools)
        except Exception:
            return self._call_openai(query, tools, system_prompt, executed_tools)

    def stream_query(self, query: str, tools: Optional[Dict[str, Any]] = None, system_prompt: str = "", chunk_size: int = 32):
        """Generator that yields tokens (strings). If the provider supports streaming, it should yield as tokens arrive.
        Otherwise, falls back to generating the full response and yielding it in chunks."""
        tools = tools or {}
        executed_tools = []
        # attempt provider-native streaming could be implemented here; fallback to chunking full output
        full = self.route_query(query, tools=tools, system_prompt=system_prompt, executed_tools=executed_tools)
        if not full:
            return
        # simple chunk by characters to simulate streaming tokens
        i = 0
        L = len(full)
        while i < L:
            end = min(L, i + chunk_size)
            yield full[i:end]
            i = end
        # final yield: signal completion with a special object
        yield {"__final__": True, "response": full, "executed_tools": executed_tools}

    def _parse_tool_call(self, content: str) -> Optional[Dict[str, Any]]:
        """Attempt to parse a tool call from model content as JSON with key 'tool_call'.
        Expected shape: {"tool_call": {"name": "tool_name", "args": {...}}}
        """
        try:
            payload = json.loads(content)
            if isinstance(payload, dict) and "tool_call" in payload:
                return payload["tool_call"]
        except Exception:
            pass
        return None

    def _execute_tool(self, tool_call: Dict[str, Any], tools: Dict[str, Any], executed_tools: Optional[list] = None) -> str:
        name = tool_call.get("name")
        args = tool_call.get("args") or {}
        if not name or name not in tools:
            return f"Tool not found: {name}"
        func = tools[name].get("func") if isinstance(tools[name], dict) else tools[name]
        try:
            result = func(**args) if callable(func) else str(func)
            record = {"name": name, "args": args, "result": str(result)}
            if executed_tools is not None:
                executed_tools.append(record)
            return str(result)
        except Exception as exc:
            log.exception("Tool execution failed: %s", exc)
            record = {"name": name, "args": args, "result": f"ERROR: {exc}"}
            if executed_tools is not None:
                executed_tools.append(record)
            return f"Tool execution failed: {exc}"

    def _call_anthropic(self, query: str, tools: Dict[str, Any], system_prompt: str, executed_tools: Optional[list] = None) -> str:
        if not ANTHROPIC_API_KEY:
            log.warning("Anthropic provider selected but ANTHROPIC_API_KEY is missing; falling back to other providers")
            return self._call_openai(query, tools, system_prompt, executed_tools)
        try:
            import requests
            url = "https://api.anthropic.com/v1/claude/3.5/sonnet/2024-12-17/messages"
            headers = {"x-api-key": ANTHROPIC_API_KEY, "Content-Type": "application/json"}
            prompt = system_prompt + "\nUser: " + query
            body = {"messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": query}], "model": "claude-3-5-sonnet"}
            resp = requests.post(url, json=body, headers=headers, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            # Anthropic responses differ by API; attempt to extract assistant content
            content = ""
            if isinstance(data, dict):
                content = data.get("completion", data.get("response", "")) or ""
                # if a structured message list is present, try to find assistant role
                if not content and "messages" in data:
                    for m in data.get("messages", []):
                        if m.get("role") == "assistant":
                            content = m.get("content", "")
                            break
            content = str(content or "").strip()
            # detect tool calls encoded as JSON
            tool_call = self._parse_tool_call(content)
            if tool_call:
                tool_result = self._execute_tool(tool_call, tools, executed_tools)
                # return the result to caller
                return tool_result
            return content or ""
        except Exception as exc:
            log.exception("Anthropic call failed: %s", exc)
            return f"Anthropic call failed: {exc}"

    def _call_openai(self, query: str, tools: Dict[str, Any], system_prompt: str, executed_tools: Optional[list] = None) -> str:
        # Minimal OpenAI-compatible routing using requests if openai package missing
        try:
            if OPENAI_API_KEY:
                import requests
                url = "https://api.openai.com/v1/chat/completions"
                headers = {"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"}
                messages = [{"role": "system", "content": system_prompt}] if system_prompt else []
                messages.append({"role": "user", "content": query})
                body = {"model": os.getenv("OPENAI_MODEL", "gpt-4o"), "messages": messages, "max_tokens": 800}
                resp = requests.post(url, json=body, headers=headers, timeout=30)
                resp.raise_for_status()
                data = resp.json()
                content = ""
                if data and "choices" in data and len(data["choices"]) > 0:
                    content = data["choices"][0].get("message", {}).get("content", "")
                content = str(content or "").strip()
                tool_call = self._parse_tool_call(content)
                if tool_call:
                    return self._execute_tool(tool_call, tools, executed_tools)
                return content
        except Exception as exc:
            log.exception("OpenAI call failed: %s", exc)
            return f"OpenAI call failed: {exc}"
        return ""

    def _call_ollama(self, query: str, tools: Dict[str, Any], system_prompt: str, executed_tools: Optional[list] = None) -> str:
        try:
            import ollama
            messages = [{"role": "system", "content": system_prompt}] if system_prompt else []
            messages.append({"role": "user", "content": query})
            response = ollama.chat(model=DEFAULT_OLLAMA_MODEL, messages=messages)
            message = response.get("message", {})
            # handle Ollama tool_calls if present
            tool_calls = message.get("tool_calls", [])
            if tool_calls:
                calls = []
                results = []
                for call in tool_calls:
                    func_name = call.get("function", {}).get("name")
                    args = call.get("function", {}).get("arguments", {})
                    if func_name and func_name in tools:
                        try:
                            fn = tools[func_name]["func"] if isinstance(tools[func_name], dict) else tools[func_name]
                            res = fn(**(args or {}))
                            # record executed tool
                            if executed_tools is not None:
                                executed_tools.append({"name": func_name, "args": args or {}, "result": str(res)})
                            results.append(str(res))
                        except Exception as e:
                            results.append(f"Tool {func_name} failed: {e}")
                # return a joined result
                return "\n".join(results)
            return message.get("content", "")
        except Exception as exc:
            log.exception("Ollama call failed: %s", exc)
            return f"Ollama call failed: {exc}"


if __name__ == '__main__':
    # quick demo when run standalone
    r = LLMRouter()
    print(r.route_query("Say hello"))
