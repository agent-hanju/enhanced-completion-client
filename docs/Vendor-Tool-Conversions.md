# 멀티 모델 대화의 벤더 도구 변환 예시

이 문서는 `uv run python examples/vendor_tool_matrix.py`가 현재 어댑터를 직접 실행해 출력한다. 네트워크 호출은 없고 벤더 응답 fixture를 실제 `to_hub()` 매퍼와 request builder에 통과시킨다. JSON은 손으로 재작성하지 않으며 `tests/test_conversion_examples.py`가 실행 결과와 이 문서의 일치를 검사한다.

한 대화의 turn 마다 다른 모델이 답한다. 각 모델은 자기 벤더에만 있는 서버 실행 도구를 쓰고, 마지막 turn만 네 벤더가 공유하는 클라이언트 함수 도구를 호출한다.

| turn | 모델 | 사용한 도구 | 도구 실행 주체 |
|---|---|---|---|
| 1 | Anthropic Messages | `web_search` | 벤더 서버 |
| 2 | OpenAI Responses | `code_interpreter` | 벤더 서버 |
| 3 | Gemini GenerateContent | `codeExecution` | 벤더 서버 |
| 4 | Chat Completions | `lookup_internal_rate` | 클라이언트 |

## 1. 도구 선언은 대상이 다르면 제외된다

네 도구를 모두 넘긴 같은 요청을 네 API로 만든다. 공용 함수 도구는 어디에나 실리고, `ToolDefinition.native(...)`로 선언한 서버 도구는 선언한 벤더의 요청에만 실린다. 다른 대상에서 일반 함수로 바꾸지 않고 제외한다. Chat Completions는 서버 실행 도구 개념이 없어 공용 함수 도구만 남는다.

<table>
<tr><th>Chat Completions</th><td><pre>[
  {
    "type": "function",
    "function": {
      "name": "lookup_internal_rate",
      "description": "사내 고시 환율을 조회한다.",
      "parameters": {
        "type": "object",
        "properties": {
          "date": {
            "type": "string"
          }
        },
        "required": [
          "date"
        ]
      }
    }
  }
]</pre></td></tr>
<tr><th>Anthropic Messages</th><td><pre>[
  {
    "name": "lookup_internal_rate",
    "description": "사내 고시 환율을 조회한다.",
    "input_schema": {
      "type": "object",
      "properties": {
        "date": {
          "type": "string"
        }
      },
      "required": [
        "date"
      ]
    }
  },
  {
    "type": "web_search_20250305",
    "name": "web_search",
    "max_uses": 3
  }
]</pre></td></tr>
<tr><th>OpenAI Responses</th><td><pre>[
  {
    "type": "function",
    "name": "lookup_internal_rate",
    "description": "사내 고시 환율을 조회한다.",
    "parameters": {
      "type": "object",
      "properties": {
        "date": {
          "type": "string"
        }
      },
      "required": [
        "date"
      ]
    }
  },
  {
    "type": "code_interpreter",
    "container": {
      "type": "auto"
    }
  }
]</pre></td></tr>
<tr><th>Gemini GenerateContent</th><td><pre>[
  {
    "functionDeclarations": [
      {
        "name": "lookup_internal_rate",
        "description": "사내 고시 환율을 조회한다.",
        "parametersJsonSchema": {
          "type": "object",
          "properties": {
            "date": {
              "type": "string"
            }
          },
          "required": [
            "date"
          ]
        }
      }
    ]
  },
  {
    "codeExecution": {}
  }
]</pre></td></tr>
</table>

## 2. 각 모델이 낸 raw 응답

turn 마다 벤더가 보낸 원본 이벤트다. 이것을 정규화한 결과는 3절의 이력에 그대로 들어 있어 여기서는 반복하지 않는다.

### Anthropic Messages

<table>
<tr><th>사용자 발화</th><td><pre>"오늘 달러 환율을 웹에서 찾아줘."</pre></td></tr>
<tr><th>수신 raw event</th><td><pre>[
  {
    "type": "message_start",
    "message": {
      "id": "msg-1",
      "model": "claude-model",
      "role": "assistant",
      "usage": {
        "input_tokens": 18,
        "output_tokens": 1
      }
    }
  },
  {
    "type": "content_block_start",
    "index": 0,
    "content_block": {
      "type": "server_tool_use",
      "id": "srvtoolu-1",
      "name": "web_search",
      "input": {}
    }
  },
  {
    "type": "content_block_delta",
    "index": 0,
    "delta": {
      "type": "input_json_delta",
      "partial_json": "{\"query\":\"USD KRW 환율\"}"
    }
  },
  {
    "type": "content_block_start",
    "index": 1,
    "content_block": {
      "type": "web_search_tool_result",
      "tool_use_id": "srvtoolu-1",
      "content": [
        {
          "type": "web_search_result",
          "url": "https://rates.example/usd-krw",
          "title": "USD/KRW"
        }
      ]
    }
  },
  {
    "type": "content_block_start",
    "index": 2,
    "content_block": {
      "type": "text",
      "text": ""
    }
  },
  {
    "type": "content_block_delta",
    "index": 2,
    "delta": {
      "type": "text_delta",
      "text": "오늘 종가는 1,383원이다."
    }
  },
  {
    "type": "message_delta",
    "delta": {
      "stop_reason": "end_turn"
    },
    "usage": {
      "output_tokens": 41
    }
  }
]</pre></td></tr>
</table>

### OpenAI Responses

<table>
<tr><th>사용자 발화</th><td><pre>"지난 30일 평균을 계산해줘."</pre></td></tr>
<tr><th>수신 raw event</th><td><pre>[
  {
    "type": "response.created",
    "response": {
      "id": "resp-1",
      "model": "responses-model"
    }
  },
  {
    "type": "response.output_item.done",
    "output_index": 0,
    "item": {
      "type": "reasoning",
      "id": "rs-1",
      "summary": [
        {
          "type": "summary_text",
          "text": "평균을 코드로 구한다."
        }
      ],
      "encrypted_content": "encrypted-reasoning"
    }
  },
  {
    "type": "response.output_item.done",
    "output_index": 1,
    "item": {
      "type": "code_interpreter_call",
      "id": "ci-1",
      "status": "completed",
      "arguments": "{\"code\":\"sum(rates)/len(rates)\"}",
      "output": "1376.4"
    }
  },
  {
    "type": "response.output_item.done",
    "output_index": 2,
    "item": {
      "type": "video_generation_call",
      "id": "vg-1",
      "status": "completed",
      "video_id": "vid-1"
    }
  },
  {
    "type": "response.output_item.done",
    "output_index": 3,
    "item": {
      "type": "message",
      "id": "msg-2",
      "role": "assistant",
      "content": [
        {
          "type": "output_text",
          "text": "30일 평균은 1,376.4원이다."
        }
      ]
    }
  },
  {
    "type": "response.completed",
    "response": {
      "status": "completed",
      "usage": {
        "input_tokens": 46,
        "output_tokens": 38
      }
    }
  }
]</pre></td></tr>
</table>

### Gemini GenerateContent

<table>
<tr><th>사용자 발화</th><td><pre>"추세를 코드로 확인해줘."</pre></td></tr>
<tr><th>수신 raw event</th><td><pre>[
  {
    "responseId": "gemini-1",
    "modelVersion": "gemini-model",
    "candidates": [
      {
        "content": {
          "role": "model",
          "parts": [
            {
              "executableCode": {
                "language": "PYTHON",
                "code": "print(trend(rates))"
              }
            },
            {
              "codeExecutionResult": {
                "outcome": "OUTCOME_OK",
                "output": "rising"
              }
            },
            {
              "text": "최근 추세는 상승이다."
            }
          ]
        },
        "finishReason": "STOP"
      }
    ],
    "usageMetadata": {
      "promptTokenCount": 72,
      "candidatesTokenCount": 33
    }
  }
]</pre></td></tr>
</table>

### Chat Completions

<table>
<tr><th>사용자 발화</th><td><pre>"사내 고시 환율도 확인해줘."</pre></td></tr>
<tr><th>수신 raw event</th><td><pre>[
  {
    "id": "chatcmpl-1",
    "model": "chat-model",
    "choices": [
      {
        "index": 0,
        "delta": {
          "role": "assistant",
          "tool_calls": [
            {
              "index": 0,
              "id": "call-1",
              "type": "function",
              "function": {
                "name": "lookup_internal_rate",
                "arguments": "{\"date\":\"2026-09-16\"}"
              }
            }
          ]
        },
        "finish_reason": "tool_calls"
      }
    ]
  }
]</pre></td></tr>
</table>

## 3. 합성된 대화 이력

네 turn을 한 이력으로 이은 `HubMessage` 목록이다. 이것이 다음 요청의 입력이다.

<table>
<tr><th>HubMessage 이력</th><td><pre>[
  {
    "role": "user",
    "content": [
      {
        "type": "text",
        "native": {},
        "text": "오늘 달러 환율을 웹에서 찾아줘.",
        "citations": []
      }
    ]
  },
  {
    "role": "assistant",
    "content": [
      {
        "type": "server_tool",
        "index": 0,
        "source": "messages",
        "native": {
          "type": "server_tool_use",
          "id": "srvtoolu-1",
          "name": "web_search",
          "input": {}
        },
        "id": "srvtoolu-1",
        "name": "web_search",
        "input_json": "{\"query\":\"USD KRW 환율\"}",
        "output": "",
        "raw": {
          "type": "server_tool_use",
          "id": "srvtoolu-1",
          "name": "web_search",
          "input": {}
        },
        "kind": "function",
        "input": {
          "query": "USD KRW 환율"
        }
      },
      {
        "type": "server_tool",
        "index": 1,
        "source": "messages",
        "native": {
          "type": "web_search_tool_result",
          "tool_use_id": "srvtoolu-1",
          "content": [
            {
              "type": "web_search_result",
              "url": "https://rates.example/usd-krw",
              "title": "USD/KRW"
            }
          ]
        },
        "id": "srvtoolu-1",
        "name": "web_search_tool_result",
        "input_json": "",
        "output": "[{\"type\": \"web_search_result\", \"url\": \"https://rates.example/usd-krw\", \"title\": \"USD/KRW\"}]",
        "raw": {
          "type": "web_search_tool_result",
          "tool_use_id": "srvtoolu-1",
          "content": [
            {
              "type": "web_search_result",
              "url": "https://rates.example/usd-krw",
              "title": "USD/KRW"
            }
          ]
        }
      },
      {
        "type": "text",
        "index": 2,
        "source": "messages",
        "native": {
          "type": "text",
          "text": ""
        },
        "text": "오늘 종가는 1,383원이다.",
        "citations": []
      }
    ]
  },
  {
    "role": "user",
    "content": [
      {
        "type": "text",
        "native": {},
        "text": "지난 30일 평균을 계산해줘.",
        "citations": []
      }
    ]
  },
  {
    "role": "assistant",
    "content": [
      {
        "type": "thinking",
        "index": 0,
        "source": "responses",
        "native": {
          "type": "reasoning",
          "id": "rs-1",
          "summary": [
            {
              "type": "summary_text",
              "text": "평균을 코드로 구한다."
            }
          ],
          "encrypted_content": "encrypted-reasoning"
        },
        "thinking": "평균을 코드로 구한다.",
        "encrypted_content": "encrypted-reasoning"
      },
      {
        "type": "server_tool",
        "index": 4294967296,
        "source": "responses",
        "native": {
          "type": "code_interpreter_call",
          "id": "ci-1",
          "status": "completed",
          "arguments": "{\"code\":\"sum(rates)/len(rates)\"}",
          "output": "1376.4"
        },
        "id": "ci-1",
        "name": "code_interpreter_call",
        "status": "completed",
        "input_json": "{\"code\":\"sum(rates)/len(rates)\"}",
        "output": "1376.4",
        "raw": {
          "type": "code_interpreter_call",
          "id": "ci-1",
          "status": "completed",
          "arguments": "{\"code\":\"sum(rates)/len(rates)\"}",
          "output": "1376.4"
        }
      },
      {
        "type": "responses_video_generation_call",
        "index": 8589934592,
        "source": "responses",
        "native": {
          "level": "item"
        },
        "raw": {
          "type": "video_generation_call",
          "id": "vg-1",
          "status": "completed",
          "video_id": "vid-1"
        }
      },
      {
        "type": "text",
        "index": 12884901888,
        "source": "responses",
        "native": {
          "item": {
            "type": "message",
            "id": "msg-2",
            "role": "assistant"
          },
          "part": {
            "type": "output_text",
            "text": "30일 평균은 1,376.4원이다."
          }
        },
        "text": "30일 평균은 1,376.4원이다.",
        "citations": []
      }
    ]
  },
  {
    "role": "user",
    "content": [
      {
        "type": "text",
        "native": {},
        "text": "추세를 코드로 확인해줘.",
        "citations": []
      }
    ]
  },
  {
    "role": "assistant",
    "content": [
      {
        "type": "server_tool",
        "index": 0,
        "source": "generate_content",
        "native": {
          "executableCode": {
            "language": "PYTHON",
            "code": "print(trend(rates))"
          }
        },
        "id": "",
        "name": "executableCode",
        "input_json": "{\"language\": \"PYTHON\", \"code\": \"print(trend(rates))\"}",
        "output": "",
        "raw": {
          "language": "PYTHON",
          "code": "print(trend(rates))"
        }
      },
      {
        "type": "server_tool",
        "index": 1,
        "source": "generate_content",
        "native": {
          "codeExecutionResult": {
            "outcome": "OUTCOME_OK",
            "output": "rising"
          }
        },
        "id": "",
        "name": "codeExecutionResult",
        "input_json": "",
        "output": "rising",
        "raw": {
          "outcome": "OUTCOME_OK",
          "output": "rising"
        }
      },
      {
        "type": "text",
        "index": 2,
        "source": "generate_content",
        "native": {
          "text": "최근 추세는 상승이다."
        },
        "text": "최근 추세는 상승이다.",
        "citations": []
      }
    ]
  },
  {
    "role": "user",
    "content": [
      {
        "type": "text",
        "native": {},
        "text": "사내 고시 환율도 확인해줘.",
        "citations": []
      }
    ]
  },
  {
    "role": "assistant",
    "content": [
      {
        "type": "tool_use",
        "index": 1,
        "source": "chat_completions",
        "native": {
          "index": 0,
          "id": "call-1",
          "type": "function",
          "function": {
            "name": "lookup_internal_rate",
            "arguments": "{\"date\":\"2026-09-16\"}"
          }
        },
        "id": "call-1",
        "name": "lookup_internal_rate",
        "kind": "function",
        "input_json": "{\"date\":\"2026-09-16\"}",
        "input": {
          "date": "2026-09-16"
        }
      }
    ]
  },
  {
    "role": "user",
    "content": [
      {
        "type": "tool_result",
        "native": {},
        "tool_use_id": "call-1",
        "name": "lookup_internal_rate",
        "kind": "function",
        "content": "{\"rate\":1380.0}",
        "structured_content": {
          "rate": 1380.0
        },
        "blocks": []
      }
    ]
  }
]</pre></td></tr>
</table>

## 4. 네 API로 보낼 때의 변환 결과

같은 이력을 네 API의 요청 대화로 직렬화한다. 도구 스키마는 1절과 같아 생략한다.

이미 실행이 끝난 서버 도구는 사라지지 않는다. 원래 벤더로 되보내면 `server_tool_use`나 `executableCode` 원형이 복원되고, 다른 벤더로 보내면 벤더 접두사가 붙은 일반 함수 호출과 그 결과 한 쌍으로 풀린다. 실행 권한을 주는 선언만 차단되고 실행된 사실은 보존된다는 뜻이다.

허브에 대응물이 없는 미등록 블록은 원 벤더로만 돌아간다. `responses_video_generation_call` 블록이 남는 대상: OpenAI Responses.

<table>
<tr><th>Chat Completions</th><td><pre>{
  "messages": [
    {
      "role": "user",
      "content": "오늘 달러 환율을 웹에서 찾아줘."
    },
    {
      "role": "assistant",
      "content": null,
      "tool_calls": [
        {
          "id": "srvtoolu-1",
          "type": "function",
          "function": {
            "name": "anthropic_web_search",
            "arguments": "{\"query\":\"USD KRW 환율\"}"
          }
        }
      ]
    },
    {
      "role": "tool",
      "tool_call_id": "srvtoolu-1",
      "content": ""
    },
    {
      "role": "assistant",
      "content": null,
      "tool_calls": [
        {
          "id": "srvtoolu-1",
          "type": "function",
          "function": {
            "name": "anthropic_web_search_tool_result",
            "arguments": "{}"
          }
        }
      ]
    },
    {
      "role": "tool",
      "tool_call_id": "srvtoolu-1",
      "content": "[{\"type\": \"web_search_result\", \"url\": \"https://rates.example/usd-krw\", \"title\": \"USD/KRW\"}]"
    },
    {
      "role": "assistant",
      "content": "오늘 종가는 1,383원이다."
    },
    {
      "role": "user",
      "content": "지난 30일 평균을 계산해줘."
    },
    {
      "role": "assistant",
      "content": null,
      "tool_calls": [
        {
          "id": "ci-1",
          "type": "function",
          "function": {
            "name": "openai_code_interpreter_call",
            "arguments": "{\"code\":\"sum(rates)/len(rates)\"}"
          }
        }
      ]
    },
    {
      "role": "tool",
      "tool_call_id": "ci-1",
      "content": "1376.4"
    },
    {
      "role": "assistant",
      "content": "30일 평균은 1,376.4원이다."
    },
    {
      "role": "user",
      "content": "추세를 코드로 확인해줘."
    },
    {
      "role": "assistant",
      "content": null,
      "tool_calls": [
        {
          "id": "executableCode_0",
          "type": "function",
          "function": {
            "name": "gemini_executableCode",
            "arguments": "{\"language\": \"PYTHON\", \"code\": \"print(trend(rates))\"}"
          }
        }
      ]
    },
    {
      "role": "tool",
      "tool_call_id": "executableCode_0",
      "content": ""
    },
    {
      "role": "assistant",
      "content": null,
      "tool_calls": [
        {
          "id": "codeExecutionResult_1",
          "type": "function",
          "function": {
            "name": "gemini_codeExecutionResult",
            "arguments": "{}"
          }
        }
      ]
    },
    {
      "role": "tool",
      "tool_call_id": "codeExecutionResult_1",
      "content": "rising"
    },
    {
      "role": "assistant",
      "content": "최근 추세는 상승이다."
    },
    {
      "role": "user",
      "content": "사내 고시 환율도 확인해줘."
    },
    {
      "role": "assistant",
      "content": null,
      "tool_calls": [
        {
          "id": "call-1",
          "type": "function",
          "function": {
            "name": "lookup_internal_rate",
            "arguments": "{\"date\":\"2026-09-16\"}"
          }
        }
      ]
    },
    {
      "role": "tool",
      "tool_call_id": "call-1",
      "content": "{\"rate\": 1380.0}"
    }
  ]
}</pre></td></tr>
<tr><th>Anthropic Messages</th><td><pre>{
  "messages": [
    {
      "role": "user",
      "content": "오늘 달러 환율을 웹에서 찾아줘."
    },
    {
      "role": "assistant",
      "content": [
        {
          "type": "server_tool_use",
          "id": "srvtoolu-1",
          "name": "web_search",
          "input": {}
        },
        {
          "type": "web_search_tool_result",
          "tool_use_id": "srvtoolu-1",
          "content": [
            {
              "type": "web_search_result",
              "url": "https://rates.example/usd-krw",
              "title": "USD/KRW"
            }
          ]
        },
        {
          "type": "text",
          "text": "오늘 종가는 1,383원이다."
        }
      ]
    },
    {
      "role": "user",
      "content": "지난 30일 평균을 계산해줘."
    },
    {
      "role": "assistant",
      "content": [
        {
          "type": "tool_use",
          "id": "ci-1",
          "name": "openai_code_interpreter_call",
          "input": {
            "code": "sum(rates)/len(rates)"
          }
        }
      ]
    },
    {
      "role": "user",
      "content": [
        {
          "type": "tool_result",
          "tool_use_id": "ci-1",
          "content": "1376.4"
        }
      ]
    },
    {
      "role": "assistant",
      "content": "30일 평균은 1,376.4원이다."
    },
    {
      "role": "user",
      "content": "추세를 코드로 확인해줘."
    },
    {
      "role": "assistant",
      "content": [
        {
          "type": "tool_use",
          "id": "executableCode_0",
          "name": "gemini_executableCode",
          "input": {
            "language": "PYTHON",
            "code": "print(trend(rates))"
          }
        }
      ]
    },
    {
      "role": "user",
      "content": [
        {
          "type": "tool_result",
          "tool_use_id": "executableCode_0",
          "content": ""
        }
      ]
    },
    {
      "role": "assistant",
      "content": [
        {
          "type": "tool_use",
          "id": "codeExecutionResult_1",
          "name": "gemini_codeExecutionResult",
          "input": {}
        }
      ]
    },
    {
      "role": "user",
      "content": [
        {
          "type": "tool_result",
          "tool_use_id": "codeExecutionResult_1",
          "content": "rising"
        }
      ]
    },
    {
      "role": "assistant",
      "content": "최근 추세는 상승이다."
    },
    {
      "role": "user",
      "content": "사내 고시 환율도 확인해줘."
    },
    {
      "role": "assistant",
      "content": [
        {
          "type": "tool_use",
          "id": "call-1",
          "name": "lookup_internal_rate",
          "input": {
            "date": "2026-09-16"
          }
        }
      ]
    },
    {
      "role": "user",
      "content": [
        {
          "type": "tool_result",
          "tool_use_id": "call-1",
          "content": "{\"rate\":1380.0}"
        }
      ]
    }
  ]
}</pre></td></tr>
<tr><th>OpenAI Responses</th><td><pre>{
  "input": [
    {
      "type": "message",
      "role": "user",
      "content": [
        {
          "type": "input_text",
          "text": "오늘 달러 환율을 웹에서 찾아줘."
        }
      ]
    },
    {
      "type": "function_call",
      "call_id": "srvtoolu-1",
      "name": "anthropic_web_search",
      "arguments": "{\"query\":\"USD KRW 환율\"}"
    },
    {
      "type": "function_call_output",
      "call_id": "srvtoolu-1",
      "output": ""
    },
    {
      "type": "function_call",
      "call_id": "srvtoolu-1",
      "name": "anthropic_web_search_tool_result",
      "arguments": "{}"
    },
    {
      "type": "function_call_output",
      "call_id": "srvtoolu-1",
      "output": "[{\"type\": \"web_search_result\", \"url\": \"https://rates.example/usd-krw\", \"title\": \"USD/KRW\"}]"
    },
    {
      "type": "message",
      "role": "assistant",
      "content": "오늘 종가는 1,383원이다."
    },
    {
      "type": "message",
      "role": "user",
      "content": [
        {
          "type": "input_text",
          "text": "지난 30일 평균을 계산해줘."
        }
      ]
    },
    {
      "type": "reasoning",
      "id": "rs-1",
      "summary": [
        {
          "type": "summary_text",
          "text": "평균을 코드로 구한다."
        }
      ],
      "encrypted_content": "encrypted-reasoning"
    },
    {
      "type": "code_interpreter_call",
      "id": "ci-1",
      "status": "completed",
      "arguments": "{\"code\":\"sum(rates)/len(rates)\"}",
      "output": "1376.4"
    },
    {
      "type": "video_generation_call",
      "id": "vg-1",
      "status": "completed",
      "video_id": "vid-1"
    },
    {
      "type": "message",
      "role": "assistant",
      "content": "30일 평균은 1,376.4원이다."
    },
    {
      "type": "message",
      "role": "user",
      "content": [
        {
          "type": "input_text",
          "text": "추세를 코드로 확인해줘."
        }
      ]
    },
    {
      "type": "function_call",
      "call_id": "executableCode_0",
      "name": "gemini_executableCode",
      "arguments": "{\"language\": \"PYTHON\", \"code\": \"print(trend(rates))\"}"
    },
    {
      "type": "function_call_output",
      "call_id": "executableCode_0",
      "output": ""
    },
    {
      "type": "function_call",
      "call_id": "codeExecutionResult_1",
      "name": "gemini_codeExecutionResult",
      "arguments": "{}"
    },
    {
      "type": "function_call_output",
      "call_id": "codeExecutionResult_1",
      "output": "rising"
    },
    {
      "type": "message",
      "role": "assistant",
      "content": "최근 추세는 상승이다."
    },
    {
      "type": "message",
      "role": "user",
      "content": [
        {
          "type": "input_text",
          "text": "사내 고시 환율도 확인해줘."
        }
      ]
    },
    {
      "type": "function_call",
      "call_id": "call-1",
      "name": "lookup_internal_rate",
      "arguments": "{\"date\":\"2026-09-16\"}"
    },
    {
      "type": "function_call_output",
      "call_id": "call-1",
      "output": "{\"rate\": 1380.0}"
    }
  ]
}</pre></td></tr>
<tr><th>Gemini GenerateContent</th><td><pre>{
  "contents": [
    {
      "role": "user",
      "parts": [
        {
          "text": "오늘 달러 환율을 웹에서 찾아줘."
        }
      ]
    },
    {
      "role": "model",
      "parts": [
        {
          "functionCall": {
            "name": "anthropic_web_search",
            "args": {
              "query": "USD KRW 환율"
            },
            "id": "srvtoolu-1"
          }
        }
      ]
    },
    {
      "role": "user",
      "parts": [
        {
          "functionResponse": {
            "name": "anthropic_web_search",
            "response": {
              "result": ""
            },
            "id": "srvtoolu-1"
          }
        }
      ]
    },
    {
      "role": "model",
      "parts": [
        {
          "functionCall": {
            "name": "anthropic_web_search_tool_result",
            "args": {},
            "id": "srvtoolu-1"
          }
        }
      ]
    },
    {
      "role": "user",
      "parts": [
        {
          "functionResponse": {
            "name": "anthropic_web_search_tool_result",
            "response": {
              "result": "[{\"type\": \"web_search_result\", \"url\": \"https://rates.example/usd-krw\", \"title\": \"USD/KRW\"}]"
            },
            "id": "srvtoolu-1"
          }
        }
      ]
    },
    {
      "role": "model",
      "parts": [
        {
          "text": "오늘 종가는 1,383원이다."
        }
      ]
    },
    {
      "role": "user",
      "parts": [
        {
          "text": "지난 30일 평균을 계산해줘."
        }
      ]
    },
    {
      "role": "model",
      "parts": [
        {
          "functionCall": {
            "name": "openai_code_interpreter_call",
            "args": {
              "code": "sum(rates)/len(rates)"
            },
            "id": "ci-1"
          }
        }
      ]
    },
    {
      "role": "user",
      "parts": [
        {
          "functionResponse": {
            "name": "openai_code_interpreter_call",
            "response": {
              "result": "1376.4"
            },
            "id": "ci-1"
          }
        }
      ]
    },
    {
      "role": "model",
      "parts": [
        {
          "text": "30일 평균은 1,376.4원이다."
        }
      ]
    },
    {
      "role": "user",
      "parts": [
        {
          "text": "추세를 코드로 확인해줘."
        }
      ]
    },
    {
      "role": "model",
      "parts": [
        {
          "executableCode": {
            "language": "PYTHON",
            "code": "print(trend(rates))"
          }
        },
        {
          "codeExecutionResult": {
            "outcome": "OUTCOME_OK",
            "output": "rising"
          }
        },
        {
          "text": "최근 추세는 상승이다."
        }
      ]
    },
    {
      "role": "user",
      "parts": [
        {
          "text": "사내 고시 환율도 확인해줘."
        }
      ]
    },
    {
      "role": "model",
      "parts": [
        {
          "functionCall": {
            "name": "lookup_internal_rate",
            "args": {
              "date": "2026-09-16"
            },
            "id": "call-1"
          }
        }
      ]
    },
    {
      "role": "user",
      "parts": [
        {
          "functionResponse": {
            "name": "lookup_internal_rate",
            "response": {
              "rate": 1380.0
            },
            "id": "call-1"
          }
        }
      ]
    }
  ]
}</pre></td></tr>
</table>
