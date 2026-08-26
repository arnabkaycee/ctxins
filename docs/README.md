# Overview

ctxins is a context inspector for agentic harnesses that intercepts traffic between the agentic harness and the LLM API to give real time visibility into the context and token usage.
It offers a lightweight alternative to observability tools like [Langfuse](https://langfuse.com/) with a hands-off approach from the harness and the LLM API requiring zero configuration in either. 
ctxins is stateless and uses mitmproxy to intercept traffic to offer real time visibility into the context pollution and suggests token usage optimization. Recommendations based on historical context usage is also presented for baseline driven optimizations.

# Design

The design is a simple two component design. A mitmproxy component intercepts traffic and sends it over an unix pipe to the listener process. The mitm proxy component sends data to the listener process on an agreed API contract. The listener component parses and analyses the context and offers metrics to correlate context usage to token cost.
Each session inspects traffic and will not persist data by default. Users can persist session data in a jsonc format and load them for historical analysis.

