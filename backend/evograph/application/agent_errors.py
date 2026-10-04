"""Bounded public diagnostics: never serialize network or internal exceptions."""

import httpx

# Order subclasses before their bases. Codes are fixed literals, not exception
# names: a plugin-defined exception class can itself contain private information.
_FAILURES = (
    (httpx.ConnectTimeout, "httpx_connect_timeout", "网络连接超时"),
    (httpx.ReadTimeout, "httpx_read_timeout", "等待网络响应超时"),
    (httpx.WriteTimeout, "httpx_write_timeout", "发送网络请求超时"),
    (httpx.PoolTimeout, "httpx_pool_timeout", "等待可用网络连接超时"),
    (httpx.TimeoutException, "httpx_timeout", "网络请求超时"),
    (httpx.ConnectError, "httpx_connect_error", "网络连接失败"),
    (httpx.ReadError, "httpx_read_error", "读取网络响应失败"),
    (httpx.WriteError, "httpx_write_error", "发送网络请求失败"),
    (httpx.CloseError, "httpx_close_error", "关闭网络连接失败"),
    (httpx.NetworkError, "httpx_network_error", "网络通信失败"),
    (httpx.RemoteProtocolError, "httpx_remote_protocol_error", "远端网络响应协议异常"),
    (httpx.LocalProtocolError, "httpx_local_protocol_error", "本地网络请求协议异常"),
    (httpx.ProtocolError, "httpx_protocol_error", "网络通信协议异常"),
    (httpx.ProxyError, "httpx_proxy_error", "网络代理连接失败"),
    (httpx.UnsupportedProtocol, "httpx_unsupported_protocol", "网络请求协议不受支持"),
    (httpx.TransportError, "httpx_transport_error", "网络传输失败"),
    (httpx.DecodingError, "httpx_decoding_error", "网络响应解码失败"),
    (httpx.TooManyRedirects, "httpx_too_many_redirects", "网络请求重定向次数过多"),
    (httpx.RequestError, "httpx_request_error", "网络请求失败"),
    (httpx.InvalidURL, "httpx_invalid_url", "网络请求地址无效"),
    (httpx.StreamError, "httpx_stream_error", "网络数据流处理异常"),
    (httpx.HTTPError, "httpx_http_error", "网络请求异常"),
    (TypeError, "internal_type_error", "内部数据类型异常"),
    (KeyError, "internal_key_error", "内部数据字段异常"),
    (IndexError, "internal_index_error", "内部数据索引异常"),
    (AttributeError, "internal_attribute_error", "内部对象属性异常"),
    (AssertionError, "internal_assertion_error", "内部状态检查失败"),
    (OSError, "internal_io_error", "内部输入输出失败"),
    (RuntimeError, "internal_runtime_error", "内部运行异常"),
)


def agent_error_event(exc: Exception) -> dict:
    """Keep intentional validation messages; sanitize all other failure details.

    Existing adapters raise application-authored ValueErrors for HTTP statuses
    and validation. HTTPX errors must be classified before that legacy contract.
    No repr, traceback, request, URL, headers, body, or exception chain is read.
    """
    if isinstance(exc, httpx.HTTPStatusError):
        code = "httpx_http_status_error"
        status = exc.response.status_code
        # Only a standard status number is safe; never use response text/reason.
        message = (
            f"网络请求失败（HTTP {status}）"
            if type(status) is int and 100 <= status <= 599
            else "网络请求返回错误状态"
        )
    else:
        for error_type, code, message in _FAILURES:
            if isinstance(exc, error_type):
                break
        else:
            if isinstance(exc, ValueError):
                return {"type": "error", "message": str(exc)[:1000]}
            code, message = "internal_error", "内部处理异常"
    return {
        "type": "error",
        "code": code,
        "message": f"Agent 请求未完成：{message}（诊断：{code}）",
    }
