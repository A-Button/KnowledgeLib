(function (KL) {
  'use strict';

  function ApiError(message, code, status, details) {
    this.name = 'ApiError';
    this.message = message || '请求失败';
    this.code = code || 'request_failed';
    this.status = status || 0;
    this.details = details || null;
    if (Error.captureStackTrace) Error.captureStackTrace(this, ApiError);
  }
  ApiError.prototype = Object.create(Error.prototype);
  ApiError.prototype.constructor = ApiError;

  function envelopeMessage(payload, fallback) {
    if (payload && payload.error && payload.error.message) return payload.error.message;
    return fallback || '请求失败';
  }

  async function request(method, path, body) {
    var options = { method: method, headers: {} };
    if (body !== undefined) {
      options.headers['Content-Type'] = 'application/json';
      options.body = JSON.stringify(body);
    }
    var response;
    try {
      response = await fetch(path, options);
    } catch (err) {
      throw new ApiError('无法连接本地服务：' + err.message, 'network_error', 0);
    }
    var text = await response.text();
    var payload = null;
    if (text) {
      try { payload = JSON.parse(text); } catch (_) { payload = { raw: text }; }
    }
    if (!response.ok) {
      var code = payload && payload.error ? payload.error.code : 'http_' + response.status;
      throw new ApiError(envelopeMessage(payload, 'HTTP ' + response.status), code, response.status, payload);
    }
    return payload;
  }

  function dispatchBlock(raw, onEvent) {
    var name = 'message';
    var data = [];
    raw.split(/\r?\n/).forEach(function (line) {
      if (line.indexOf('event:') === 0) name = line.slice(6).trim();
      else if (line.indexOf('data:') === 0) data.push(line.slice(5).replace(/^ /, ''));
    });
    if (!data.length) return;
    var payload = data.join('\n');
    try { payload = JSON.parse(payload); } catch (_) { /* keep text */ }
    onEvent(name, payload);
  }

  async function stream(path, body, onEvent, signal) {
    var response;
    try {
      response = await fetch(path, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
        signal: signal
      });
    } catch (err) {
      if (err && err.name === 'AbortError') throw err;
      throw new ApiError('无法连接本地服务：' + err.message, 'network_error', 0);
    }
    if (!response.ok) {
      var text = await response.text();
      var payload = null;
      try { payload = JSON.parse(text); } catch (_) { payload = null; }
      throw new ApiError(envelopeMessage(payload, 'HTTP ' + response.status), payload && payload.error ? payload.error.code : 'http_' + response.status, response.status, payload);
    }
    if (!response.body) throw new ApiError('当前浏览器不支持流式响应', 'stream_unsupported', 0);

    var reader = response.body.getReader();
    var decoder = new TextDecoder('utf-8');
    var buffer = '';
    while (true) {
      var chunk = await reader.read();
      if (chunk.done) break;
      buffer += decoder.decode(chunk.value, { stream: true });
      buffer = buffer.replace(/\r\n/g, '\n');
      var boundary;
      while ((boundary = buffer.indexOf('\n\n')) >= 0) {
        var raw = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        dispatchBlock(raw, onEvent);
      }
    }
    buffer += decoder.decode();
    if (buffer.trim()) dispatchBlock(buffer, onEvent);
  }

  KL.api = {
    request: request,
    get: function (path) { return request('GET', path); },
    post: function (path, body) { return request('POST', path, body); },
    put: function (path, body) { return request('PUT', path, body); },
    del: function (path) { return request('DELETE', path); },
    stream: stream,
    ApiError: ApiError
  };
})(window.KL = window.KL || {});
