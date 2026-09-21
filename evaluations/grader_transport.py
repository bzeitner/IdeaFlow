"""Standalone provider transport, with a killable wall-clock deadline."""
import http.client
import json
import multiprocessing
import os
import time
from django.core.exceptions import ValidationError

class _AnthropicRequest:
    """One non-streaming request, no SDK retries, tools, caching or browsing."""
    def __call__(self, *, model, prompt, max_output_tokens, timeout_seconds, system):
        key = os.environ.get('ANTHROPIC_API_KEY')
        if not key:
            raise ValidationError('ANTHROPIC_API_KEY is not configured.')
        body = json.dumps({'model':model, 'max_tokens':max_output_tokens,
                           'service_tier':'standard_only','system':system,'messages':[{'role':'user','content':prompt}]})
        deadline = time.monotonic() + timeout_seconds
        connection = http.client.HTTPSConnection('api.anthropic.com',timeout=timeout_seconds)
        try:
            connection.request('POST','/v1/messages',body=body.encode(),headers={
                'x-api-key':key,'anthropic-version':'2023-06-01','content-type':'application/json'})
            connection.sock.settimeout(max(0.01,deadline-time.monotonic()))
            response = connection.getresponse()
            chunks=[]
            total=0
            while True:
                remaining=deadline-time.monotonic()
                if remaining <= 0:
                    raise TimeoutError('Grader deadline exceeded.')
                if connection.sock:
                    connection.sock.settimeout(remaining)
                chunk=response.read1(min(65536,1048577-total))
                if not chunk:
                    break
                chunks.append(chunk)
                total+=len(chunk)
                if total>1048576:
                    raise ValidationError('Grader response exceeds the byte limit.')
            raw=b''.join(chunks)
            if response.status!=200:
                # Do not copy provider error bodies or credentials into logs/audit errors.
                raise ValidationError('Grader provider request failed.')
            return raw
        finally:
            connection.close()


def _worker(pipe, arguments):
    try:
        pipe.send((True, _AnthropicRequest()(**arguments)))
    except Exception as exc:
        pipe.send((False, type(exc).__name__))
    finally:
        pipe.close()


class AnthropicGrader:
    def __call__(self, *, model, prompt, max_output_tokens, timeout_seconds):
        # Kept outside module imports so spawned worker initialization needs no
        # Django app registry and never imports production models.
        from .calibration import GRADER_SYSTEM
        context = multiprocessing.get_context('spawn')
        receive, send = context.Pipe(duplex=False)
        process = context.Process(target=_worker, args=(send, {
            'model':model,'prompt':prompt,'max_output_tokens':max_output_tokens,
            'timeout_seconds':timeout_seconds,'system':GRADER_SYSTEM}))
        try:
            process.start()
            send.close()
            if not receive.poll(timeout_seconds):
                raise TimeoutError('Grader wall-clock deadline exceeded.')
            success, value = receive.recv()
            if not success:
                raise ValidationError('Provider request failed; error content withheld.')
            return value
        finally:
            receive.close()
            send.close()
            if process.pid is not None:
                if process.is_alive():
                    process.terminate()
                process.join(timeout=2)
                if process.is_alive():
                    process.kill()
                    process.join(timeout=2)
