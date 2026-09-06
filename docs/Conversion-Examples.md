# 변환 규칙 및 실행 예시

이 문서는 `uv run python examples/conversion_matrix.py`가 현재 어댑터를 직접 실행해 출력한다. raw payload는 짧고 결정적인 fixture이며 JSON은 손으로 재작성하지 않는다. `tests/test_conversion_examples.py`가 실행 결과와 이 문서의 완전 일치를 검사한다.

모든 시나리오는 `서울 날씨를 조회해줘.` → `get_weather({city: 서울})` → `{temp_c: 25}` 순서다. 후속 요청 표는 반복되는 tool schema를 생략하고 대화 payload만 표시한다.

| 의미 | Chat Completions | Anthropic Messages | OpenAI Responses | Gemini GenerateContent |
|---|---|---|---|---|
| 도구 호출 | `assistant.tool_calls[]` | assistant의 `tool_use` | `function_call` item | model의 `functionCall` part |
| 도구 결과 | `role: tool` message | user의 `tool_result` | `function_call_output` item | user의 `functionResponse` part |
| 원 벤더 추론 복원 | `reasoning_content` | 서명된 `thinking` | 암호문 포함 `reasoning` item | `thoughtSignature` 포함 thought part |
| 다른 벤더로 이동한 추론 | 생략 | 생략 | 생략 | 생략 |

## Chat Completions에서 시작

<table>
<tr><th>최초 요청</th><td><pre>{
  "messages": [
    {
      "role": "user",
      "content": "서울 날씨를 조회해줘."
    }
  ],
  "tools": [
    {
      "type": "function",
      "function": {
        "name": "get_weather",
        "description": "도시의 현재 기온을 조회한다.",
        "parameters": {
          "type": "object",
          "properties": {
            "city": {
              "type": "string"
            }
          },
          "required": [
            "city"
          ]
        }
      }
    }
  ]
}</pre></td></tr>
<tr><th>수신 raw event</th><td><pre>[
  {
    "id": "chatcmpl-1",
    "model": "chat-model",
    "choices": [
      {
        "index": 0,
        "delta": {
          "role": "assistant",
          "reasoning_content": "날씨 도구를 호출한다.",
          "tool_calls": [
            {
              "index": 0,
              "id": "call-1",
              "type": "function",
              "function": {
                "name": "get_weather",
                "arguments": "{\"city\":\"서울\"}"
              }
            }
          ]
        },
        "finish_reason": "tool_calls"
      }
    ]
  }
]</pre></td></tr>
<tr><th>정규화된 HubResponse</th><td><pre>{
  "id": "chatcmpl-1",
  "model": "chat-model",
  "role": "assistant",
  "content": [
    {
      "type": "thinking",
      "index": -1,
      "source": "chat_completions",
      "native": {},
      "thinking": "날씨 도구를 호출한다."
    },
    {
      "type": "tool_use",
      "index": 1,
      "source": "chat_completions",
      "native": {
        "index": 0,
        "id": "call-1",
        "type": "function",
        "function": {
          "name": "get_weather",
          "arguments": "{\"city\":\"서울\"}"
        }
      },
      "id": "call-1",
      "name": "get_weather",
      "kind": "function",
      "input_json": "{\"city\":\"서울\"}",
      "input": {
        "city": "서울"
      }
    }
  ],
  "stop_reason": "tool_use"
}</pre></td></tr>
</table>

### 후속 요청 변환

<table>
<tr><th>대상</th><th>실제 request conversation</th></tr>
<tr><th>Chat Completions (원 벤더 복원)</th><td><pre>{
  "messages": [
    {
      "role": "user",
      "content": "서울 날씨를 조회해줘."
    },
    {
      "role": "assistant",
      "content": null,
      "tool_calls": [
        {
          "id": "call-1",
          "type": "function",
          "function": {
            "name": "get_weather",
            "arguments": "{\"city\":\"서울\"}"
          }
        }
      ],
      "reasoning_content": "날씨 도구를 호출한다."
    },
    {
      "role": "tool",
      "tool_call_id": "call-1",
      "content": "{\"temp_c\": 25}"
    }
  ]
}</pre></td></tr>
<tr><th>Anthropic Messages</th><td><pre>{
  "messages": [
    {
      "role": "user",
      "content": "서울 날씨를 조회해줘."
    },
    {
      "role": "assistant",
      "content": [
        {
          "type": "tool_use",
          "id": "call-1",
          "name": "get_weather",
          "input": {
            "city": "서울"
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
          "content": "{\"temp_c\":25}"
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
          "text": "서울 날씨를 조회해줘."
        }
      ]
    },
    {
      "type": "function_call",
      "call_id": "call-1",
      "name": "get_weather",
      "arguments": "{\"city\":\"서울\"}"
    },
    {
      "type": "function_call_output",
      "call_id": "call-1",
      "output": "{\"temp_c\": 25}"
    }
  ]
}</pre></td></tr>
<tr><th>Gemini GenerateContent</th><td><pre>{
  "contents": [
    {
      "role": "user",
      "parts": [
        {
          "text": "서울 날씨를 조회해줘."
        }
      ]
    },
    {
      "role": "model",
      "parts": [
        {
          "functionCall": {
            "name": "get_weather",
            "args": {
              "city": "서울"
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
            "name": "get_weather",
            "response": {
              "temp_c": 25
            },
            "id": "call-1"
          }
        }
      ]
    }
  ]
}</pre></td></tr>
</table>

## Anthropic Messages에서 시작

<table>
<tr><th>최초 요청</th><td><pre>{
  "messages": [
    {
      "role": "user",
      "content": "서울 날씨를 조회해줘."
    }
  ],
  "tools": [
    {
      "name": "get_weather",
      "description": "도시의 현재 기온을 조회한다.",
      "input_schema": {
        "type": "object",
        "properties": {
          "city": {
            "type": "string"
          }
        },
        "required": [
          "city"
        ]
      }
    }
  ]
}</pre></td></tr>
<tr><th>수신 raw event</th><td><pre>[
  {
    "type": "message_start",
    "message": {
      "id": "msg-1",
      "model": "claude-model",
      "role": "assistant",
      "usage": {
        "input_tokens": 12,
        "output_tokens": 1
      }
    }
  },
  {
    "type": "content_block_start",
    "index": 0,
    "content_block": {
      "type": "thinking",
      "thinking": ""
    }
  },
  {
    "type": "content_block_delta",
    "index": 0,
    "delta": {
      "type": "thinking_delta",
      "thinking": "날씨 도구를 호출한다."
    }
  },
  {
    "type": "content_block_delta",
    "index": 0,
    "delta": {
      "type": "signature_delta",
      "signature": "anthropic-signature"
    }
  },
  {
    "type": "content_block_start",
    "index": 1,
    "content_block": {
      "type": "tool_use",
      "id": "call-1",
      "name": "get_weather",
      "input": {}
    }
  },
  {
    "type": "content_block_delta",
    "index": 1,
    "delta": {
      "type": "input_json_delta",
      "partial_json": "{\"city\":\"서울\"}"
    }
  },
  {
    "type": "message_delta",
    "delta": {
      "stop_reason": "tool_use"
    },
    "usage": {
      "output_tokens": 24
    }
  }
]</pre></td></tr>
<tr><th>정규화된 HubResponse</th><td><pre>{
  "id": "msg-1",
  "model": "claude-model",
  "role": "assistant",
  "content": [
    {
      "type": "thinking",
      "index": 0,
      "source": "messages",
      "native": {
        "type": "thinking",
        "thinking": ""
      },
      "thinking": "날씨 도구를 호출한다.",
      "signature": "anthropic-signature"
    },
    {
      "type": "tool_use",
      "index": 1,
      "source": "messages",
      "native": {
        "type": "tool_use",
        "id": "call-1",
        "name": "get_weather",
        "input": {}
      },
      "id": "call-1",
      "name": "get_weather",
      "kind": "function",
      "input_json": "{\"city\":\"서울\"}",
      "input": {
        "city": "서울"
      }
    }
  ],
  "stop_reason": "tool_use",
  "usage": {
    "input_tokens": 12,
    "output_tokens": 24
  }
}</pre></td></tr>
</table>

### 후속 요청 변환

<table>
<tr><th>대상</th><th>실제 request conversation</th></tr>
<tr><th>Chat Completions</th><td><pre>{
  "messages": [
    {
      "role": "user",
      "content": "서울 날씨를 조회해줘."
    },
    {
      "role": "assistant",
      "content": null,
      "tool_calls": [
        {
          "id": "call-1",
          "type": "function",
          "function": {
            "name": "get_weather",
            "arguments": "{\"city\":\"서울\"}"
          }
        }
      ]
    },
    {
      "role": "tool",
      "tool_call_id": "call-1",
      "content": "{\"temp_c\": 25}"
    }
  ]
}</pre></td></tr>
<tr><th>Anthropic Messages (원 벤더 복원)</th><td><pre>{
  "messages": [
    {
      "role": "user",
      "content": "서울 날씨를 조회해줘."
    },
    {
      "role": "assistant",
      "content": [
        {
          "type": "thinking",
          "thinking": "날씨 도구를 호출한다.",
          "signature": "anthropic-signature"
        },
        {
          "type": "tool_use",
          "id": "call-1",
          "name": "get_weather",
          "input": {
            "city": "서울"
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
          "content": "{\"temp_c\":25}"
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
          "text": "서울 날씨를 조회해줘."
        }
      ]
    },
    {
      "type": "function_call",
      "call_id": "call-1",
      "name": "get_weather",
      "arguments": "{\"city\":\"서울\"}"
    },
    {
      "type": "function_call_output",
      "call_id": "call-1",
      "output": "{\"temp_c\": 25}"
    }
  ]
}</pre></td></tr>
<tr><th>Gemini GenerateContent</th><td><pre>{
  "contents": [
    {
      "role": "user",
      "parts": [
        {
          "text": "서울 날씨를 조회해줘."
        }
      ]
    },
    {
      "role": "model",
      "parts": [
        {
          "functionCall": {
            "name": "get_weather",
            "args": {
              "city": "서울"
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
            "name": "get_weather",
            "response": {
              "temp_c": 25
            },
            "id": "call-1"
          }
        }
      ]
    }
  ]
}</pre></td></tr>
</table>

## OpenAI Responses에서 시작

<table>
<tr><th>최초 요청</th><td><pre>{
  "input": [
    {
      "type": "message",
      "role": "user",
      "content": [
        {
          "type": "input_text",
          "text": "서울 날씨를 조회해줘."
        }
      ]
    }
  ],
  "tools": [
    {
      "type": "function",
      "name": "get_weather",
      "description": "도시의 현재 기온을 조회한다.",
      "parameters": {
        "type": "object",
        "properties": {
          "city": {
            "type": "string"
          }
        },
        "required": [
          "city"
        ]
      }
    }
  ]
}</pre></td></tr>
<tr><th>수신 raw event</th><td><pre>[
  {
    "type": "response.created",
    "response": {
      "id": "resp-1",
      "model": "responses-model"
    }
  },
  {
    "type": "response.output_item.added",
    "output_index": 0,
    "item": {
      "type": "reasoning",
      "id": "rs-1",
      "summary": []
    }
  },
  {
    "type": "response.reasoning_summary_text.delta",
    "output_index": 0,
    "summary_index": 0,
    "delta": "날씨 도구를 호출한다."
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
          "text": "날씨 도구를 호출한다."
        }
      ],
      "encrypted_content": "encrypted-reasoning"
    }
  },
  {
    "type": "response.output_item.added",
    "output_index": 1,
    "item": {
      "type": "function_call",
      "id": "fc-1",
      "call_id": "call-1",
      "name": "get_weather",
      "arguments": ""
    }
  },
  {
    "type": "response.function_call_arguments.delta",
    "output_index": 1,
    "delta": "{\"city\":\"서울\"}"
  },
  {
    "type": "response.output_item.done",
    "output_index": 1,
    "item": {
      "type": "function_call",
      "id": "fc-1",
      "call_id": "call-1",
      "name": "get_weather",
      "arguments": "{\"city\":\"서울\"}",
      "status": "completed"
    }
  },
  {
    "type": "response.completed",
    "response": {
      "status": "completed",
      "usage": {
        "input_tokens": 12,
        "output_tokens": 24
      }
    }
  }
]</pre></td></tr>
<tr><th>정규화된 HubResponse</th><td><pre>{
  "id": "resp-1",
  "model": "responses-model",
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
            "text": "날씨 도구를 호출한다."
          }
        ],
        "encrypted_content": "encrypted-reasoning"
      },
      "thinking": "날씨 도구를 호출한다.",
      "encrypted_content": "encrypted-reasoning"
    },
    {
      "type": "tool_use",
      "index": 4294967296,
      "source": "responses",
      "native": {
        "type": "function_call",
        "id": "fc-1",
        "call_id": "call-1",
        "name": "get_weather",
        "arguments": "{\"city\":\"서울\"}",
        "status": "completed"
      },
      "id": "call-1",
      "name": "get_weather",
      "kind": "function",
      "input_json": "{\"city\":\"서울\"}",
      "input": {
        "city": "서울"
      }
    }
  ],
  "stop_reason": "end_turn",
  "usage": {
    "input_tokens": 12,
    "output_tokens": 24
  }
}</pre></td></tr>
</table>

### 후속 요청 변환

<table>
<tr><th>대상</th><th>실제 request conversation</th></tr>
<tr><th>Chat Completions</th><td><pre>{
  "messages": [
    {
      "role": "user",
      "content": "서울 날씨를 조회해줘."
    },
    {
      "role": "assistant",
      "content": null,
      "tool_calls": [
        {
          "id": "call-1",
          "type": "function",
          "function": {
            "name": "get_weather",
            "arguments": "{\"city\":\"서울\"}"
          }
        }
      ]
    },
    {
      "role": "tool",
      "tool_call_id": "call-1",
      "content": "{\"temp_c\": 25}"
    }
  ]
}</pre></td></tr>
<tr><th>Anthropic Messages</th><td><pre>{
  "messages": [
    {
      "role": "user",
      "content": "서울 날씨를 조회해줘."
    },
    {
      "role": "assistant",
      "content": [
        {
          "type": "tool_use",
          "id": "call-1",
          "name": "get_weather",
          "input": {
            "city": "서울"
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
          "content": "{\"temp_c\":25}"
        }
      ]
    }
  ]
}</pre></td></tr>
<tr><th>OpenAI Responses (원 벤더 복원)</th><td><pre>{
  "input": [
    {
      "type": "message",
      "role": "user",
      "content": [
        {
          "type": "input_text",
          "text": "서울 날씨를 조회해줘."
        }
      ]
    },
    {
      "type": "reasoning",
      "id": "rs-1",
      "summary": [
        {
          "type": "summary_text",
          "text": "날씨 도구를 호출한다."
        }
      ],
      "encrypted_content": "encrypted-reasoning"
    },
    {
      "type": "function_call",
      "id": "fc-1",
      "call_id": "call-1",
      "name": "get_weather",
      "arguments": "{\"city\":\"서울\"}",
      "status": "completed"
    },
    {
      "type": "function_call_output",
      "call_id": "call-1",
      "output": "{\"temp_c\": 25}"
    }
  ]
}</pre></td></tr>
<tr><th>Gemini GenerateContent</th><td><pre>{
  "contents": [
    {
      "role": "user",
      "parts": [
        {
          "text": "서울 날씨를 조회해줘."
        }
      ]
    },
    {
      "role": "model",
      "parts": [
        {
          "functionCall": {
            "name": "get_weather",
            "args": {
              "city": "서울"
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
            "name": "get_weather",
            "response": {
              "temp_c": 25
            },
            "id": "call-1"
          }
        }
      ]
    }
  ]
}</pre></td></tr>
</table>

## Gemini GenerateContent에서 시작

<table>
<tr><th>최초 요청</th><td><pre>{
  "contents": [
    {
      "role": "user",
      "parts": [
        {
          "text": "서울 날씨를 조회해줘."
        }
      ]
    }
  ],
  "tools": [
    {
      "functionDeclarations": [
        {
          "name": "get_weather",
          "description": "도시의 현재 기온을 조회한다.",
          "parameters": {
            "type": "object",
            "properties": {
              "city": {
                "type": "string"
              }
            },
            "required": [
              "city"
            ]
          }
        }
      ]
    }
  ]
}</pre></td></tr>
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
              "text": "날씨 도구를 호출한다.",
              "thought": true,
              "thoughtSignature": "gemini-thought-signature"
            },
            {
              "functionCall": {
                "id": "call-1",
                "name": "get_weather",
                "args": {
                  "city": "서울"
                }
              },
              "thoughtSignature": "gemini-call-signature"
            }
          ]
        },
        "finishReason": "STOP"
      }
    ],
    "usageMetadata": {
      "promptTokenCount": 12,
      "candidatesTokenCount": 24
    }
  }
]</pre></td></tr>
<tr><th>정규화된 HubResponse</th><td><pre>{
  "id": "gemini-1",
  "model": "gemini-model",
  "role": "assistant",
  "content": [
    {
      "type": "thinking",
      "index": 0,
      "source": "generate_content",
      "native": {
        "text": "날씨 도구를 호출한다.",
        "thought": true,
        "thoughtSignature": "gemini-thought-signature"
      },
      "thinking": "날씨 도구를 호출한다.",
      "signature": "gemini-thought-signature"
    },
    {
      "type": "tool_use",
      "index": 1,
      "source": "generate_content",
      "native": {
        "functionCall": {
          "id": "call-1",
          "name": "get_weather",
          "args": {
            "city": "서울"
          }
        },
        "thoughtSignature": "gemini-call-signature"
      },
      "id": "call-1",
      "name": "get_weather",
      "kind": "function",
      "input_json": "{\"city\": \"서울\"}",
      "input": {
        "city": "서울"
      },
      "signature": "gemini-call-signature"
    }
  ],
  "stop_reason": "end_turn",
  "usage": {
    "input_tokens": 12,
    "output_tokens": 24
  }
}</pre></td></tr>
</table>

### 후속 요청 변환

<table>
<tr><th>대상</th><th>실제 request conversation</th></tr>
<tr><th>Chat Completions</th><td><pre>{
  "messages": [
    {
      "role": "user",
      "content": "서울 날씨를 조회해줘."
    },
    {
      "role": "assistant",
      "content": null,
      "tool_calls": [
        {
          "id": "call-1",
          "type": "function",
          "function": {
            "name": "get_weather",
            "arguments": "{\"city\": \"서울\"}"
          }
        }
      ]
    },
    {
      "role": "tool",
      "tool_call_id": "call-1",
      "content": "{\"temp_c\": 25}"
    }
  ]
}</pre></td></tr>
<tr><th>Anthropic Messages</th><td><pre>{
  "messages": [
    {
      "role": "user",
      "content": "서울 날씨를 조회해줘."
    },
    {
      "role": "assistant",
      "content": [
        {
          "type": "tool_use",
          "id": "call-1",
          "name": "get_weather",
          "input": {
            "city": "서울"
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
          "content": "{\"temp_c\":25}"
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
          "text": "서울 날씨를 조회해줘."
        }
      ]
    },
    {
      "type": "function_call",
      "call_id": "call-1",
      "name": "get_weather",
      "arguments": "{\"city\": \"서울\"}"
    },
    {
      "type": "function_call_output",
      "call_id": "call-1",
      "output": "{\"temp_c\": 25}"
    }
  ]
}</pre></td></tr>
<tr><th>Gemini GenerateContent (원 벤더 복원)</th><td><pre>{
  "contents": [
    {
      "role": "user",
      "parts": [
        {
          "text": "서울 날씨를 조회해줘."
        }
      ]
    },
    {
      "role": "model",
      "parts": [
        {
          "text": "날씨 도구를 호출한다.",
          "thought": true,
          "thoughtSignature": "gemini-thought-signature"
        },
        {
          "functionCall": {
            "name": "get_weather",
            "args": {
              "city": "서울"
            },
            "id": "call-1"
          },
          "thoughtSignature": "gemini-call-signature"
        }
      ]
    },
    {
      "role": "user",
      "parts": [
        {
          "functionResponse": {
            "name": "get_weather",
            "response": {
              "temp_c": 25
            },
            "id": "call-1"
          }
        }
      ]
    }
  ]
}</pre></td></tr>
</table>

---

# 다양한 content 및 ReAct 변환 예시

이 문서는 `uv run python examples/rich_content_matrix.py`가 현재 구현을 직접 실행한 결과다. 네트워크 호출 없이 벤더 응답 fixture를 실제 `to_hub()` 매퍼와 request builder에 통과시킨다. JSON은 손으로 재작성하지 않으며 `tests/test_conversion_examples.py`가 실행 결과와 기록 문서의 완전 일치를 검사한다.

`원 벤더 복원`은 응답을 `HubMessage.of_response()`로 옮겨 같은 벤더의 다음 요청 이력으로 직렬화한 결과다.

## 변환 정책 요약

| 블록 | 같은 벤더 | 다른 벤더 |
|---|---|---|
| text | native 메타데이터와 함께 복원 | text로 보존 |
| thinking/reasoning | signature/encrypted payload까지 복원 | 생략 |
| image/document 입력 | URL/inline data를 native part로 변환 | 미지원 채널은 생략 또는 text fallback |
| audio 입력 | Chat/Gemini native part | Anthropic/Responses에서는 생략 |
| output audio | data/transcript 수집; 원격 audio ID는 재생 거부 | 다른 벤더에서는 생략 |
| generic function call/result | 원 형식 복원 | 대상의 tool call/result로 변환 |
| 환경 의존 call/result | 수신 block을 파싱하되 자동 활성화하지 않음 | 생략 |
| server tool block | 수신 결과와 순서 파싱; 활성화는 별도 `tools` 입력 | 생략 |
| XML/Anthropic citation | `TextBlock.citations`에 원형 구분, 가능한 입력만 복원 | 명시적 XML 어휘가 없으면 생략 |
| OpenAI/Gemini annotation | Responses native 이력만 복원 | 다른 요청에서는 생략 |
| Gemini grounding graph | Hub에 source/support 관계 보존 | 모든 요청 이력에서 생략 |

## 1. 멀티모달 사용자 입력

text, base64 image, base64 audio, base64 PDF, 평문 문서를 한 HubMessage에 넣었다.

<table>
<tr><th>HubMessage</th><td><pre>{
  "role": "user",
  "content": [
    {
      "type": "text",
      "native": {},
      "text": "다음 자료를 함께 분석해.",
      "citations": []
    },
    {
      "type": "image",
      "native": {},
      "media_type": "image/png",
      "data": "iVBORw0KGgo=",
      "detail": "low",
      "serialize": false
    },
    {
      "type": "audio",
      "native": {},
      "serialize": false,
      "data": "UklGRg==",
      "format": "wav",
      "media_type": "audio/wav"
    },
    {
      "type": "document",
      "native": {},
      "serialize": false,
      "id": "report.pdf",
      "title": "보고서.pdf",
      "text": "",
      "data": "JVBERi0xLjQ=",
      "media_type": "application/pdf",
      "citations_enabled": true
    },
    {
      "type": "document",
      "native": {},
      "serialize": false,
      "id": "facts",
      "title": "사실표",
      "text": "서울=25도",
      "media_type": "text/plain",
      "citations_enabled": true
    }
  ]
}</pre></td></tr>
<tr><th>Chat Completions</th><td><pre>{
  "messages": [
    {
      "role": "user",
      "content": [
        {
          "type": "text",
          "text": "다음 자료를 함께 분석해."
        },
        {
          "type": "image_url",
          "image_url": {
            "url": "data:image/png;base64,iVBORw0KGgo=",
            "detail": "low"
          }
        },
        {
          "type": "input_audio",
          "input_audio": {
            "data": "UklGRg==",
            "format": "wav"
          }
        },
        {
          "type": "file",
          "file": {
            "file_data": "data:application/pdf;base64,JVBERi0xLjQ=",
            "filename": "보고서.pdf"
          }
        },
        {
          "type": "text",
          "text": "&lt;documents&gt;\n&lt;document id=\"facts\"&gt;\n&lt;title&gt;사실표&lt;/title&gt;\n&lt;content&gt;서울=25도&lt;/content&gt;\n&lt;/document&gt;\n&lt;/documents&gt;"
        }
      ]
    }
  ]
}</pre></td></tr>
<tr><th>Anthropic Messages</th><td><pre>{
  "messages": [
    {
      "role": "user",
      "content": [
        {
          "type": "text",
          "text": "다음 자료를 함께 분석해."
        },
        {
          "type": "image",
          "source": {
            "type": "base64",
            "media_type": "image/png",
            "data": "iVBORw0KGgo="
          }
        },
        {
          "type": "text",
          "text": "&lt;attachments&gt;\n&lt;audio media-type=\"audio/wav\" unavailable=\"true\"&gt;이 형식은 이 요청에 전달할 수 없다&lt;/audio&gt;\n&lt;/attachments&gt;"
        },
        {
          "type": "document",
          "source": {
            "type": "base64",
            "media_type": "application/pdf",
            "data": "JVBERi0xLjQ="
          },
          "title": "보고서.pdf",
          "citations": {
            "enabled": true
          }
        },
        {
          "type": "document",
          "source": {
            "type": "text",
            "media_type": "text/plain",
            "data": "서울=25도"
          },
          "title": "사실표",
          "citations": {
            "enabled": true
          }
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
          "text": "다음 자료를 함께 분석해."
        },
        {
          "type": "input_image",
          "image_url": "data:image/png;base64,iVBORw0KGgo="
        },
        {
          "type": "input_text",
          "text": "&lt;attachments&gt;\n&lt;audio media-type=\"audio/wav\" unavailable=\"true\"&gt;이 형식은 이 요청에 전달할 수 없다&lt;/audio&gt;\n&lt;/attachments&gt;"
        },
        {
          "type": "input_file",
          "file_data": "data:application/pdf;base64,JVBERi0xLjQ=",
          "filename": "보고서.pdf"
        },
        {
          "type": "input_text",
          "text": "&lt;documents&gt;\n&lt;document id=\"facts\"&gt;\n&lt;title&gt;사실표&lt;/title&gt;\n&lt;content&gt;서울=25도&lt;/content&gt;\n&lt;/document&gt;\n&lt;/documents&gt;"
        }
      ]
    }
  ]
}</pre></td></tr>
<tr><th>Gemini GenerateContent</th><td><pre>{
  "contents": [
    {
      "role": "user",
      "parts": [
        {
          "text": "다음 자료를 함께 분석해."
        },
        {
          "inlineData": {
            "mimeType": "image/png",
            "data": "iVBORw0KGgo="
          }
        },
        {
          "inlineData": {
            "mimeType": "audio/wav",
            "data": "UklGRg=="
          }
        },
        {
          "inlineData": {
            "mimeType": "application/pdf",
            "data": "JVBERi0xLjQ="
          }
        },
        {
          "text": "&lt;documents&gt;\n&lt;document id=\"facts\"&gt;\n&lt;title&gt;사실표&lt;/title&gt;\n&lt;content&gt;서울=25도&lt;/content&gt;\n&lt;/document&gt;\n&lt;/documents&gt;"
        }
      ]
    }
  ]
}</pre></td></tr>
</table>

## 2. 같은 타입의 독립 block 반복

같은 메시지 안의 두 text block을 합치지 않고 네 요청 형식으로 내렸다. 단, 실제 스트림에서 이 구분을 복원할 수 있는지는 원 프로토콜이 part index를 주는지에 달려 있다.

<table>
<tr><th>HubMessage</th><td><pre>{
  "role": "assistant",
  "content": [
    {
      "type": "text",
      "native": {},
      "text": "첫 번째 text block",
      "citations": []
    },
    {
      "type": "text",
      "native": {},
      "text": "두 번째 text block",
      "citations": []
    }
  ]
}</pre></td></tr>
<tr><th>Chat Completions</th><td><pre>{
  "messages": [
    {
      "role": "assistant",
      "content": [
        {
          "type": "text",
          "text": "첫 번째 text block"
        },
        {
          "type": "text",
          "text": "두 번째 text block"
        }
      ]
    }
  ]
}</pre></td></tr>
<tr><th>Anthropic Messages</th><td><pre>{
  "messages": [
    {
      "role": "assistant",
      "content": [
        {
          "type": "text",
          "text": "첫 번째 text block"
        },
        {
          "type": "text",
          "text": "두 번째 text block"
        }
      ]
    }
  ]
}</pre></td></tr>
<tr><th>OpenAI Responses</th><td><pre>{
  "input": [
    {
      "type": "message",
      "role": "assistant",
      "content": "첫 번째 text block두 번째 text block"
    }
  ]
}</pre></td></tr>
<tr><th>Gemini GenerateContent</th><td><pre>{
  "contents": [
    {
      "role": "model",
      "parts": [
        {
          "text": "첫 번째 text block"
        },
        {
          "text": "두 번째 text block"
        }
      ]
    }
  ]
}</pre></td></tr>
</table>

## 3. 여러 턴의 ReAct tool loop

두 번의 function call/result와 최종 text까지 role, call ID와 턴 순서를 보존한다.

<table>
<tr><th>Hub history</th><td><pre>[
  {
    "role": "assistant",
    "content": [
      {
        "type": "tool_use",
        "native": {},
        "id": "call-1",
        "name": "lookup",
        "kind": "function",
        "input_json": "{\"step\":1}",
        "input": {
          "step": 1
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
        "name": "lookup",
        "kind": "function",
        "content": "first",
        "blocks": []
      }
    ]
  },
  {
    "role": "assistant",
    "content": [
      {
        "type": "tool_use",
        "native": {},
        "id": "call-2",
        "name": "lookup",
        "kind": "function",
        "input_json": "{\"step\":2}",
        "input": {
          "step": 2
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
        "tool_use_id": "call-2",
        "name": "lookup",
        "kind": "function",
        "content": "second",
        "blocks": []
      }
    ]
  },
  {
    "role": "assistant",
    "content": [
      {
        "type": "text",
        "native": {},
        "text": "완료",
        "citations": []
      }
    ]
  }
]</pre></td></tr>
<tr><th>Chat Completions</th><td><pre>{
  "messages": [
    {
      "role": "assistant",
      "content": null,
      "tool_calls": [
        {
          "id": "call-1",
          "type": "function",
          "function": {
            "name": "lookup",
            "arguments": "{\"step\":1}"
          }
        }
      ]
    },
    {
      "role": "tool",
      "tool_call_id": "call-1",
      "content": "first"
    },
    {
      "role": "assistant",
      "content": null,
      "tool_calls": [
        {
          "id": "call-2",
          "type": "function",
          "function": {
            "name": "lookup",
            "arguments": "{\"step\":2}"
          }
        }
      ]
    },
    {
      "role": "tool",
      "tool_call_id": "call-2",
      "content": "second"
    },
    {
      "role": "assistant",
      "content": "완료"
    }
  ]
}</pre></td></tr>
<tr><th>Anthropic Messages</th><td><pre>{
  "messages": [
    {
      "role": "assistant",
      "content": [
        {
          "type": "tool_use",
          "id": "call-1",
          "name": "lookup",
          "input": {
            "step": 1
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
          "content": "first"
        }
      ]
    },
    {
      "role": "assistant",
      "content": [
        {
          "type": "tool_use",
          "id": "call-2",
          "name": "lookup",
          "input": {
            "step": 2
          }
        }
      ]
    },
    {
      "role": "user",
      "content": [
        {
          "type": "tool_result",
          "tool_use_id": "call-2",
          "content": "second"
        }
      ]
    },
    {
      "role": "assistant",
      "content": "완료"
    }
  ]
}</pre></td></tr>
<tr><th>OpenAI Responses</th><td><pre>{
  "input": [
    {
      "type": "function_call",
      "call_id": "call-1",
      "name": "lookup",
      "arguments": "{\"step\":1}"
    },
    {
      "type": "function_call_output",
      "call_id": "call-1",
      "output": "first"
    },
    {
      "type": "function_call",
      "call_id": "call-2",
      "name": "lookup",
      "arguments": "{\"step\":2}"
    },
    {
      "type": "function_call_output",
      "call_id": "call-2",
      "output": "second"
    },
    {
      "type": "message",
      "role": "assistant",
      "content": "완료"
    }
  ]
}</pre></td></tr>
<tr><th>Gemini GenerateContent</th><td><pre>{
  "contents": [
    {
      "role": "model",
      "parts": [
        {
          "functionCall": {
            "name": "lookup",
            "args": {
              "step": 1
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
            "name": "lookup",
            "response": {
              "result": "first"
            },
            "id": "call-1"
          }
        }
      ]
    },
    {
      "role": "model",
      "parts": [
        {
          "functionCall": {
            "name": "lookup",
            "args": {
              "step": 2
            },
            "id": "call-2"
          }
        }
      ]
    },
    {
      "role": "user",
      "parts": [
        {
          "functionResponse": {
            "name": "lookup",
            "response": {
              "result": "second"
            },
            "id": "call-2"
          }
        }
      ]
    },
    {
      "role": "model",
      "parts": [
        {
          "text": "완료"
        }
      ]
    }
  ]
}</pre></td></tr>
</table>

## 4. 이미지와 문서가 포함된 tool result

`content`, `structured_content`, image block, PDF block을 한 tool result에 넣었다.

<table>
<tr><th>HubMessage history</th><td><pre>[
  {
    "role": "assistant",
    "content": [
      {
        "type": "tool_use",
        "native": {},
        "id": "call-media",
        "name": "capture",
        "kind": "function",
        "input_json": "{\"city\":\"서울\"}",
        "input": {
          "city": "서울"
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
        "tool_use_id": "call-media",
        "name": "capture",
        "kind": "function",
        "content": "관측 결과",
        "structured_content": {
          "temp_c": 25
        },
        "blocks": [
          {
            "type": "image",
            "native": {},
            "media_type": "image/png",
            "data": "iVBORw0KGgo=",
            "serialize": false
          },
          {
            "type": "document",
            "native": {},
            "serialize": false,
            "id": "result.pdf",
            "title": "결과.pdf",
            "text": "",
            "data": "JVBERi0xLjQ=",
            "media_type": "application/pdf",
            "citations_enabled": true
          }
        ]
      }
    ]
  }
]</pre></td></tr>
<tr><th>Chat Completions</th><td><pre>{
  "messages": [
    {
      "role": "assistant",
      "content": null,
      "tool_calls": [
        {
          "id": "call-media",
          "type": "function",
          "function": {
            "name": "capture",
            "arguments": "{\"city\":\"서울\"}"
          }
        }
      ]
    },
    {
      "role": "tool",
      "tool_call_id": "call-media",
      "content": "관측 결과\n&lt;documents&gt;\n&lt;document id=\"result.pdf\"&gt;\n&lt;title&gt;결과.pdf&lt;/title&gt;\n&lt;content media-type=\"application/pdf\" unavailable=\"true\"&gt;이 형식은 이 요청에 전달할 수 없다&lt;/content&gt;\n&lt;/document&gt;\n&lt;/documents&gt;\n&lt;attachments&gt;\n&lt;image media-type=\"image/png\" unavailable=\"true\"&gt;이 형식은 이 요청에 전달할 수 없다&lt;/image&gt;\n&lt;/attachments&gt;"
    }
  ]
}</pre></td></tr>
<tr><th>Anthropic Messages</th><td><pre>{
  "messages": [
    {
      "role": "assistant",
      "content": [
        {
          "type": "tool_use",
          "id": "call-media",
          "name": "capture",
          "input": {
            "city": "서울"
          }
        }
      ]
    },
    {
      "role": "user",
      "content": [
        {
          "type": "tool_result",
          "tool_use_id": "call-media",
          "content": [
            {
              "type": "text",
              "text": "관측 결과"
            },
            {
              "type": "image",
              "source": {
                "type": "base64",
                "media_type": "image/png",
                "data": "iVBORw0KGgo="
              }
            },
            {
              "type": "document",
              "source": {
                "type": "base64",
                "media_type": "application/pdf",
                "data": "JVBERi0xLjQ="
              },
              "title": "결과.pdf"
            }
          ]
        }
      ]
    }
  ]
}</pre></td></tr>
<tr><th>OpenAI Responses</th><td><pre>{
  "input": [
    {
      "type": "function_call",
      "call_id": "call-media",
      "name": "capture",
      "arguments": "{\"city\":\"서울\"}"
    },
    {
      "type": "function_call_output",
      "call_id": "call-media",
      "output": [
        {
          "type": "input_text",
          "text": "관측 결과"
        },
        {
          "type": "input_image",
          "image_url": "data:image/png;base64,iVBORw0KGgo="
        },
        {
          "type": "input_file",
          "file_data": "data:application/pdf;base64,JVBERi0xLjQ=",
          "filename": "결과.pdf"
        }
      ]
    }
  ]
}</pre></td></tr>
<tr><th>Gemini GenerateContent</th><td><pre>{
  "contents": [
    {
      "role": "model",
      "parts": [
        {
          "functionCall": {
            "name": "capture",
            "args": {
              "city": "서울"
            },
            "id": "call-media"
          }
        }
      ]
    },
    {
      "role": "user",
      "parts": [
        {
          "functionResponse": {
            "name": "capture",
            "response": {
              "temp_c": 25
            },
            "id": "call-media",
            "parts": [
              {
                "inlineData": {
                  "mimeType": "image/png",
                  "data": "iVBORw0KGgo="
                }
              },
              {
                "inlineData": {
                  "mimeType": "application/pdf",
                  "data": "JVBERi0xLjQ="
                }
              }
            ]
          }
        }
      ]
    }
  ]
}</pre></td></tr>
</table>

## 5. 벤더별 rich response 왕복

### 5.1. Chat Completions

포함 요소: reasoning_content, text, output audio, URL citation.

<table>
<tr><th>수신 raw event</th><td><pre>[
  {
    "id": "chatcmpl-rich-1",
    "model": "chat-model",
    "choices": [
      {
        "index": 0,
        "delta": {
          "role": "assistant",
          "reasoning_content": "이미지와 근거를 결합한다.",
          "content": "서울은 25도다.",
          "audio": {
            "id": "audio-1",
            "data": "UklGRg==",
            "transcript": "서울은 25도다.",
            "expires_at": 1893456000
          },
          "annotations": [
            {
              "type": "url_citation",
              "url_citation": {
                "start_index": 0,
                "end_index": 2,
                "url": "https://weather.example/seoul",
                "title": "서울 관측"
              }
            }
          ]
        },
        "finish_reason": "stop"
      }
    ],
    "usage": {
      "prompt_tokens": 18,
      "completion_tokens": 12
    }
  }
]</pre></td></tr>
<tr><th>정규화된 HubResponse</th><td><pre>{
  "id": "chatcmpl-rich-1",
  "model": "chat-model",
  "role": "assistant",
  "content": [
    {
      "type": "thinking",
      "index": -1,
      "source": "chat_completions",
      "native": {},
      "thinking": "이미지와 근거를 결합한다."
    },
    {
      "type": "text",
      "index": 0,
      "source": "chat_completions",
      "native": {},
      "text": "서울은 25도다.",
      "citations": []
    },
    {
      "type": "audio",
      "index": -3,
      "source": "chat_completions",
      "native": {
        "id": "audio-1",
        "data": "UklGRg==",
        "transcript": "서울은 25도다.",
        "expires_at": 1893456000
      },
      "serialize": false,
      "data": "UklGRg==",
      "file_id": "audio-1",
      "transcript": "서울은 25도다.",
      "expires_at": 1893456000
    },
    {
      "type": "annotation",
      "source": "chat_completions",
      "native": {
        "type": "url_citation",
        "url_citation": {
          "start_index": 0,
          "end_index": 2,
          "url": "https://weather.example/seoul",
          "title": "서울 관측"
        }
      },
      "annotation_index": 0,
      "target_index": 0,
      "kind": "url_citation",
      "id": "https://weather.example/seoul",
      "start_index": 0,
      "end_index": 2,
      "title": "서울 관측",
      "uri": "https://weather.example/seoul"
    }
  ],
  "stop_reason": "end_turn",
  "usage": {
    "input_tokens": 18,
    "output_tokens": 12
  }
}</pre></td></tr>
</table>

#### 네 벤더의 다음 요청 이력으로 변환

<table>
<tr><th>대상</th><th>실제 request conversation</th></tr>
<tr><th>Chat Completions (원 벤더 복원)</th><td><pre>{
  "rejected": "file_id based content is not supported; use a URL or inline base64 data"
}</pre></td></tr>
<tr><th>Anthropic Messages</th><td><pre>{
  "messages": [
    {
      "role": "user",
      "content": [
        {
          "type": "text",
          "text": "다음 자료를 함께 분석해."
        },
        {
          "type": "image",
          "source": {
            "type": "base64",
            "media_type": "image/png",
            "data": "iVBORw0KGgo="
          }
        },
        {
          "type": "text",
          "text": "&lt;attachments&gt;\n&lt;audio media-type=\"audio/wav\" unavailable=\"true\"&gt;이 형식은 이 요청에 전달할 수 없다&lt;/audio&gt;\n&lt;/attachments&gt;"
        },
        {
          "type": "document",
          "source": {
            "type": "base64",
            "media_type": "application/pdf",
            "data": "JVBERi0xLjQ="
          },
          "title": "보고서.pdf",
          "citations": {
            "enabled": true
          }
        },
        {
          "type": "document",
          "source": {
            "type": "text",
            "media_type": "text/plain",
            "data": "서울=25도"
          },
          "title": "사실표",
          "citations": {
            "enabled": true
          }
        }
      ]
    },
    {
      "role": "assistant",
      "content": "서울은 25도다.\n\n&lt;references&gt;\n&lt;reference kind=\"url_citation\" uri=\"https://weather.example/seoul\" title=\"서울 관측\"/&gt;\n&lt;/references&gt;"
    },
    {
      "role": "user",
      "content": "한 줄로 다시 정리해줘."
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
          "text": "다음 자료를 함께 분석해."
        },
        {
          "type": "input_image",
          "image_url": "data:image/png;base64,iVBORw0KGgo="
        },
        {
          "type": "input_text",
          "text": "&lt;attachments&gt;\n&lt;audio media-type=\"audio/wav\" unavailable=\"true\"&gt;이 형식은 이 요청에 전달할 수 없다&lt;/audio&gt;\n&lt;/attachments&gt;"
        },
        {
          "type": "input_file",
          "file_data": "data:application/pdf;base64,JVBERi0xLjQ=",
          "filename": "보고서.pdf"
        },
        {
          "type": "input_text",
          "text": "&lt;documents&gt;\n&lt;document id=\"facts\"&gt;\n&lt;title&gt;사실표&lt;/title&gt;\n&lt;content&gt;서울=25도&lt;/content&gt;\n&lt;/document&gt;\n&lt;/documents&gt;"
        }
      ]
    },
    {
      "type": "message",
      "role": "assistant",
      "content": "서울은 25도다.\n\n&lt;references&gt;\n&lt;reference kind=\"url_citation\" uri=\"https://weather.example/seoul\" title=\"서울 관측\"/&gt;\n&lt;/references&gt;"
    },
    {
      "type": "message",
      "role": "user",
      "content": [
        {
          "type": "input_text",
          "text": "한 줄로 다시 정리해줘."
        }
      ]
    }
  ]
}</pre></td></tr>
<tr><th>Gemini GenerateContent</th><td><pre>{
  "contents": [
    {
      "role": "user",
      "parts": [
        {
          "text": "다음 자료를 함께 분석해."
        },
        {
          "inlineData": {
            "mimeType": "image/png",
            "data": "iVBORw0KGgo="
          }
        },
        {
          "inlineData": {
            "mimeType": "audio/wav",
            "data": "UklGRg=="
          }
        },
        {
          "inlineData": {
            "mimeType": "application/pdf",
            "data": "JVBERi0xLjQ="
          }
        },
        {
          "text": "&lt;documents&gt;\n&lt;document id=\"facts\"&gt;\n&lt;title&gt;사실표&lt;/title&gt;\n&lt;content&gt;서울=25도&lt;/content&gt;\n&lt;/document&gt;\n&lt;/documents&gt;"
        }
      ]
    },
    {
      "role": "model",
      "parts": [
        {
          "text": "서울은 25도다.\n\n&lt;references&gt;\n&lt;reference kind=\"url_citation\" uri=\"https://weather.example/seoul\" title=\"서울 관측\"/&gt;\n&lt;/references&gt;"
        }
      ]
    },
    {
      "role": "user",
      "parts": [
        {
          "text": "한 줄로 다시 정리해줘."
        }
      ]
    }
  ]
}</pre></td></tr>
</table>

### 5.2. Anthropic Messages

포함 요소: signed thinking, citation, server tool/result, code result, container-uploaded file ID.

<table>
<tr><th>수신 raw event</th><td><pre>[
  {
    "type": "message_start",
    "message": {
      "id": "msg-rich-1",
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
      "type": "thinking",
      "thinking": ""
    }
  },
  {
    "type": "content_block_delta",
    "index": 0,
    "delta": {
      "type": "thinking_delta",
      "thinking": "웹 근거를 확인한다."
    }
  },
  {
    "type": "content_block_delta",
    "index": 0,
    "delta": {
      "type": "signature_delta",
      "signature": "anthropic-rich-signature"
    }
  },
  {
    "type": "content_block_start",
    "index": 1,
    "content_block": {
      "type": "text",
      "text": ""
    }
  },
  {
    "type": "content_block_delta",
    "index": 1,
    "delta": {
      "type": "text_delta",
      "text": "서울은 25도다."
    }
  },
  {
    "type": "content_block_delta",
    "index": 1,
    "delta": {
      "type": "citations_delta",
      "citation": {
        "type": "char_location",
        "cited_text": "서울은 25도",
        "document_index": 0,
        "document_title": "기상 문서",
        "start_char_index": 0,
        "end_char_index": 7
      }
    }
  },
  {
    "type": "content_block_start",
    "index": 2,
    "content_block": {
      "type": "server_tool_use",
      "id": "srv-search-1",
      "name": "web_search",
      "input": {
        "query": "서울 현재 기온"
      }
    }
  },
  {
    "type": "content_block_start",
    "index": 3,
    "content_block": {
      "type": "web_search_tool_result",
      "tool_use_id": "srv-search-1",
      "content": [
        {
          "type": "web_search_result",
          "url": "https://weather.example/seoul",
          "title": "서울 관측",
          "encrypted_content": "anthropic-search-result",
          "page_age": "today"
        }
      ]
    }
  },
  {
    "type": "content_block_start",
    "index": 4,
    "content_block": {
      "type": "code_execution_tool_result",
      "tool_use_id": "srv-code-1",
      "content": {
        "type": "code_execution_result",
        "content": [
          {
            "type": "code_execution_output",
            "file_id": "file_1"
          }
        ],
        "return_code": 0,
        "stderr": "",
        "stdout": "25\n"
      }
    }
  },
  {
    "type": "content_block_start",
    "index": 5,
    "content_block": {
      "type": "container_upload",
      "file_id": "file_1"
    }
  },
  {
    "type": "message_delta",
    "delta": {
      "stop_reason": "end_turn"
    },
    "usage": {
      "output_tokens": 32
    }
  }
]</pre></td></tr>
<tr><th>정규화된 HubResponse</th><td><pre>{
  "id": "msg-rich-1",
  "model": "claude-model",
  "role": "assistant",
  "content": [
    {
      "type": "thinking",
      "index": 0,
      "source": "messages",
      "native": {
        "type": "thinking",
        "thinking": ""
      },
      "thinking": "웹 근거를 확인한다.",
      "signature": "anthropic-rich-signature"
    },
    {
      "type": "text",
      "index": 1,
      "source": "messages",
      "native": {
        "type": "text",
        "text": ""
      },
      "text": "서울은 25도다.",
      "citations": [
        {
          "type": "char_location",
          "source": "messages",
          "id": "기상 문서",
          "cited_text": "서울은 25도",
          "document_index": 0,
          "document_title": "기상 문서",
          "source_start": 0,
          "source_end": 7,
          "native": {
            "type": "char_location",
            "cited_text": "서울은 25도",
            "document_index": 0,
            "document_title": "기상 문서",
            "start_char_index": 0,
            "end_char_index": 7
          }
        }
      ]
    },
    {
      "type": "server_tool",
      "index": 2,
      "source": "messages",
      "native": {
        "type": "server_tool_use",
        "id": "srv-search-1",
        "name": "web_search",
        "input": {
          "query": "서울 현재 기온"
        }
      },
      "id": "srv-search-1",
      "name": "web_search",
      "input_json": "",
      "output": "",
      "raw": {
        "type": "server_tool_use",
        "id": "srv-search-1",
        "name": "web_search",
        "input": {
          "query": "서울 현재 기온"
        }
      }
    },
    {
      "type": "server_tool",
      "index": 3,
      "source": "messages",
      "native": {
        "type": "web_search_tool_result",
        "tool_use_id": "srv-search-1",
        "content": [
          {
            "type": "web_search_result",
            "url": "https://weather.example/seoul",
            "title": "서울 관측",
            "encrypted_content": "anthropic-search-result",
            "page_age": "today"
          }
        ]
      },
      "id": "srv-search-1",
      "name": "web_search_tool_result",
      "input_json": "",
      "output": "[{\"type\": \"web_search_result\", \"url\": \"https://weather.example/seoul\", \"title\": \"서울 관측\", \"encrypted_content\": \"anthropic-search-result\", \"page_age\": \"today\"}]",
      "raw": {
        "type": "web_search_tool_result",
        "tool_use_id": "srv-search-1",
        "content": [
          {
            "type": "web_search_result",
            "url": "https://weather.example/seoul",
            "title": "서울 관측",
            "encrypted_content": "anthropic-search-result",
            "page_age": "today"
          }
        ]
      }
    },
    {
      "type": "server_tool",
      "index": 4,
      "source": "messages",
      "native": {
        "type": "code_execution_tool_result",
        "tool_use_id": "srv-code-1",
        "content": {
          "type": "code_execution_result",
          "content": [
            {
              "type": "code_execution_output",
              "file_id": "file_1"
            }
          ],
          "return_code": 0,
          "stderr": "",
          "stdout": "25\n"
        }
      },
      "id": "srv-code-1",
      "name": "code_execution_tool_result",
      "input_json": "",
      "output": "{\"type\": \"code_execution_result\", \"content\": [{\"type\": \"code_execution_output\", \"file_id\": \"file_1\"}], \"return_code\": 0, \"stderr\": \"\", \"stdout\": \"25\\n\"}",
      "raw": {
        "type": "code_execution_tool_result",
        "tool_use_id": "srv-code-1",
        "content": {
          "type": "code_execution_result",
          "content": [
            {
              "type": "code_execution_output",
              "file_id": "file_1"
            }
          ],
          "return_code": 0,
          "stderr": "",
          "stdout": "25\n"
        }
      }
    },
    {
      "type": "container_upload",
      "index": 5,
      "source": "messages",
      "native": {
        "type": "container_upload",
        "file_id": "file_1"
      },
      "raw": {
        "type": "container_upload",
        "file_id": "file_1"
      }
    }
  ],
  "stop_reason": "end_turn",
  "usage": {
    "input_tokens": 18,
    "output_tokens": 32
  }
}</pre></td></tr>
</table>

#### 네 벤더의 다음 요청 이력으로 변환

<table>
<tr><th>대상</th><th>실제 request conversation</th></tr>
<tr><th>Chat Completions</th><td><pre>{
  "messages": [
    {
      "role": "user",
      "content": [
        {
          "type": "text",
          "text": "다음 자료를 함께 분석해."
        },
        {
          "type": "image_url",
          "image_url": {
            "url": "data:image/png;base64,iVBORw0KGgo=",
            "detail": "low"
          }
        },
        {
          "type": "input_audio",
          "input_audio": {
            "data": "UklGRg==",
            "format": "wav"
          }
        },
        {
          "type": "file",
          "file": {
            "file_data": "data:application/pdf;base64,JVBERi0xLjQ=",
            "filename": "보고서.pdf"
          }
        },
        {
          "type": "text",
          "text": "&lt;documents&gt;\n&lt;document id=\"facts\"&gt;\n&lt;title&gt;사실표&lt;/title&gt;\n&lt;content&gt;서울=25도&lt;/content&gt;\n&lt;/document&gt;\n&lt;/documents&gt;"
        }
      ]
    },
    {
      "role": "assistant",
      "content": "&lt;cite id=\"기상 문서\"&gt;서울은 25도다.&lt;/cite&gt;",
      "tool_calls": [
        {
          "id": "srv-search-1",
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
      "tool_call_id": "srv-search-1",
      "content": "[{\"type\": \"web_search_result\", \"url\": \"https://weather.example/seoul\", \"title\": \"서울 관측\", \"encrypted_content\": \"anthropic-search-result\", \"page_age\": \"today\"}]"
    },
    {
      "role": "assistant",
      "content": null,
      "tool_calls": [
        {
          "id": "srv-code-1",
          "type": "function",
          "function": {
            "name": "anthropic_code_execution_tool_result",
            "arguments": "{}"
          }
        }
      ]
    },
    {
      "role": "tool",
      "tool_call_id": "srv-code-1",
      "content": "{\"type\": \"code_execution_result\", \"content\": [{\"type\": \"code_execution_output\", \"file_id\": \"file_1\"}], \"return_code\": 0, \"stderr\": \"\", \"stdout\": \"25\\n\"}"
    },
    {
      "role": "user",
      "content": "한 줄로 다시 정리해줘."
    }
  ]
}</pre></td></tr>
<tr><th>Anthropic Messages (원 벤더 복원)</th><td><pre>{
  "rejected": "remote file/container references cannot be replayed; materialize them as URL or inline data"
}</pre></td></tr>
<tr><th>OpenAI Responses</th><td><pre>{
  "input": [
    {
      "type": "message",
      "role": "user",
      "content": [
        {
          "type": "input_text",
          "text": "다음 자료를 함께 분석해."
        },
        {
          "type": "input_image",
          "image_url": "data:image/png;base64,iVBORw0KGgo="
        },
        {
          "type": "input_text",
          "text": "&lt;attachments&gt;\n&lt;audio media-type=\"audio/wav\" unavailable=\"true\"&gt;이 형식은 이 요청에 전달할 수 없다&lt;/audio&gt;\n&lt;/attachments&gt;"
        },
        {
          "type": "input_file",
          "file_data": "data:application/pdf;base64,JVBERi0xLjQ=",
          "filename": "보고서.pdf"
        },
        {
          "type": "input_text",
          "text": "&lt;documents&gt;\n&lt;document id=\"facts\"&gt;\n&lt;title&gt;사실표&lt;/title&gt;\n&lt;content&gt;서울=25도&lt;/content&gt;\n&lt;/document&gt;\n&lt;/documents&gt;"
        }
      ]
    },
    {
      "type": "message",
      "role": "assistant",
      "content": "&lt;cite id=\"기상 문서\"&gt;서울은 25도다.&lt;/cite&gt;"
    },
    {
      "type": "function_call",
      "call_id": "srv-search-1",
      "name": "anthropic_web_search_tool_result",
      "arguments": "{}"
    },
    {
      "type": "function_call_output",
      "call_id": "srv-search-1",
      "output": "[{\"type\": \"web_search_result\", \"url\": \"https://weather.example/seoul\", \"title\": \"서울 관측\", \"encrypted_content\": \"anthropic-search-result\", \"page_age\": \"today\"}]"
    },
    {
      "type": "function_call",
      "call_id": "srv-code-1",
      "name": "anthropic_code_execution_tool_result",
      "arguments": "{}"
    },
    {
      "type": "function_call_output",
      "call_id": "srv-code-1",
      "output": "{\"type\": \"code_execution_result\", \"content\": [{\"type\": \"code_execution_output\", \"file_id\": \"file_1\"}], \"return_code\": 0, \"stderr\": \"\", \"stdout\": \"25\\n\"}"
    },
    {
      "type": "message",
      "role": "user",
      "content": [
        {
          "type": "input_text",
          "text": "한 줄로 다시 정리해줘."
        }
      ]
    }
  ]
}</pre></td></tr>
<tr><th>Gemini GenerateContent</th><td><pre>{
  "contents": [
    {
      "role": "user",
      "parts": [
        {
          "text": "다음 자료를 함께 분석해."
        },
        {
          "inlineData": {
            "mimeType": "image/png",
            "data": "iVBORw0KGgo="
          }
        },
        {
          "inlineData": {
            "mimeType": "audio/wav",
            "data": "UklGRg=="
          }
        },
        {
          "inlineData": {
            "mimeType": "application/pdf",
            "data": "JVBERi0xLjQ="
          }
        },
        {
          "text": "&lt;documents&gt;\n&lt;document id=\"facts\"&gt;\n&lt;title&gt;사실표&lt;/title&gt;\n&lt;content&gt;서울=25도&lt;/content&gt;\n&lt;/document&gt;\n&lt;/documents&gt;"
        }
      ]
    },
    {
      "role": "model",
      "parts": [
        {
          "text": "&lt;cite id=\"기상 문서\"&gt;서울은 25도다.&lt;/cite&gt;"
        },
        {
          "functionCall": {
            "name": "anthropic_web_search_tool_result",
            "args": {},
            "id": "srv-search-1"
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
              "result": "[{\"type\": \"web_search_result\", \"url\": \"https://weather.example/seoul\", \"title\": \"서울 관측\", \"encrypted_content\": \"anthropic-search-result\", \"page_age\": \"today\"}]"
            },
            "id": "srv-search-1"
          }
        }
      ]
    },
    {
      "role": "model",
      "parts": [
        {
          "functionCall": {
            "name": "anthropic_code_execution_tool_result",
            "args": {},
            "id": "srv-code-1"
          }
        }
      ]
    },
    {
      "role": "user",
      "parts": [
        {
          "functionResponse": {
            "name": "anthropic_code_execution_tool_result",
            "response": {
              "result": "{\"type\": \"code_execution_result\", \"content\": [{\"type\": \"code_execution_output\", \"file_id\": \"file_1\"}], \"return_code\": 0, \"stderr\": \"\", \"stdout\": \"25\\n\"}"
            },
            "id": "srv-code-1"
          }
        }
      ]
    },
    {
      "role": "user",
      "parts": [
        {
          "text": "한 줄로 다시 정리해줘."
        }
      ]
    }
  ]
}</pre></td></tr>
</table>

### 5.3. OpenAI Responses

포함 요소: encrypted reasoning, text, global audio stream, annotation, web/code server tools.

<table>
<tr><th>수신 raw event</th><td><pre>[
  {
    "type": "response.created",
    "response": {
      "id": "resp-rich-1",
      "model": "responses-model"
    }
  },
  {
    "type": "response.output_item.done",
    "output_index": 0,
    "item": {
      "type": "reasoning",
      "id": "rs-rich-1",
      "summary": [
        {
          "type": "summary_text",
          "text": "웹 결과를 비교했다."
        }
      ],
      "encrypted_content": "responses-encrypted-reasoning"
    }
  },
  {
    "type": "response.output_item.done",
    "output_index": 1,
    "item": {
      "type": "message",
      "id": "msg-responses-1",
      "role": "assistant",
      "status": "completed",
      "content": [
        {
          "type": "output_text",
          "text": "서울은 25도다.",
          "annotations": [
            {
              "type": "url_citation",
              "start_index": 0,
              "end_index": 2,
              "url": "https://weather.example/seoul",
              "title": "서울 관측"
            }
          ]
        }
      ]
    }
  },
  {
    "type": "response.audio.delta",
    "delta": "UklGRg==",
    "sequence_number": 0
  },
  {
    "type": "response.audio.transcript.delta",
    "delta": "서울은 25도다.",
    "sequence_number": 1
  },
  {
    "type": "response.output_text.annotation.added",
    "output_index": 1,
    "content_index": 0,
    "annotation": {
      "type": "url_citation",
      "start_index": 0,
      "end_index": 2,
      "url": "https://weather.example/seoul",
      "title": "서울 관측"
    }
  },
  {
    "type": "response.output_item.done",
    "output_index": 2,
    "item": {
      "type": "web_search_call",
      "id": "ws-rich-1",
      "status": "completed",
      "action": {
        "type": "search",
        "query": "서울 현재 기온"
      }
    }
  },
  {
    "type": "response.output_item.done",
    "output_index": 3,
    "item": {
      "type": "code_interpreter_call",
      "id": "ci-rich-1",
      "status": "completed",
      "code": "print(25)",
      "outputs": [
        {
          "type": "logs",
          "logs": "25\n"
        }
      ]
    }
  },
  {
    "type": "response.completed",
    "response": {
      "status": "completed",
      "usage": {
        "input_tokens": 18,
        "output_tokens": 32
      }
    }
  }
]</pre></td></tr>
<tr><th>정규화된 HubResponse</th><td><pre>{
  "id": "resp-rich-1",
  "model": "responses-model",
  "role": "assistant",
  "content": [
    {
      "type": "thinking",
      "index": 0,
      "source": "responses",
      "native": {
        "type": "reasoning",
        "id": "rs-rich-1",
        "summary": [
          {
            "type": "summary_text",
            "text": "웹 결과를 비교했다."
          }
        ],
        "encrypted_content": "responses-encrypted-reasoning"
      },
      "thinking": "웹 결과를 비교했다.",
      "encrypted_content": "responses-encrypted-reasoning"
    },
    {
      "type": "text",
      "index": 4294967296,
      "source": "responses",
      "native": {
        "item": {
          "type": "message",
          "id": "msg-responses-1",
          "role": "assistant",
          "status": "completed"
        },
        "part": {
          "type": "output_text",
          "text": "서울은 25도다.",
          "annotations": [
            {
              "type": "url_citation",
              "start_index": 0,
              "end_index": 2,
              "url": "https://weather.example/seoul",
              "title": "서울 관측"
            }
          ]
        }
      },
      "text": "서울은 25도다.",
      "citations": []
    },
    {
      "type": "annotation",
      "source": "responses",
      "native": {
        "type": "url_citation",
        "start_index": 0,
        "end_index": 2,
        "url": "https://weather.example/seoul",
        "title": "서울 관측"
      },
      "annotation_index": 281474976710656,
      "target_index": 4294967296,
      "kind": "url_citation",
      "id": "https://weather.example/seoul",
      "start_index": 0,
      "end_index": 2,
      "title": "서울 관측",
      "uri": "https://weather.example/seoul"
    },
    {
      "type": "audio",
      "index": -1,
      "source": "responses",
      "native": {},
      "serialize": false,
      "data": "UklGRg==",
      "transcript": "서울은 25도다."
    },
    {
      "type": "server_tool",
      "index": 8589934592,
      "source": "responses",
      "native": {
        "type": "web_search_call",
        "id": "ws-rich-1",
        "status": "completed",
        "action": {
          "type": "search",
          "query": "서울 현재 기온"
        }
      },
      "id": "ws-rich-1",
      "name": "web_search_call",
      "status": "completed",
      "input_json": "",
      "output": "",
      "raw": {
        "type": "web_search_call",
        "id": "ws-rich-1",
        "status": "completed",
        "action": {
          "type": "search",
          "query": "서울 현재 기온"
        }
      }
    },
    {
      "type": "server_tool",
      "index": 12884901888,
      "source": "responses",
      "native": {
        "type": "code_interpreter_call",
        "id": "ci-rich-1",
        "status": "completed",
        "code": "print(25)",
        "outputs": [
          {
            "type": "logs",
            "logs": "25\n"
          }
        ]
      },
      "id": "ci-rich-1",
      "name": "code_interpreter_call",
      "status": "completed",
      "input_json": "",
      "output": "",
      "raw": {
        "type": "code_interpreter_call",
        "id": "ci-rich-1",
        "status": "completed",
        "code": "print(25)",
        "outputs": [
          {
            "type": "logs",
            "logs": "25\n"
          }
        ]
      }
    }
  ],
  "stop_reason": "end_turn",
  "usage": {
    "input_tokens": 18,
    "output_tokens": 32
  }
}</pre></td></tr>
</table>

#### 네 벤더의 다음 요청 이력으로 변환

<table>
<tr><th>대상</th><th>실제 request conversation</th></tr>
<tr><th>Chat Completions</th><td><pre>{
  "messages": [
    {
      "role": "user",
      "content": [
        {
          "type": "text",
          "text": "다음 자료를 함께 분석해."
        },
        {
          "type": "image_url",
          "image_url": {
            "url": "data:image/png;base64,iVBORw0KGgo=",
            "detail": "low"
          }
        },
        {
          "type": "input_audio",
          "input_audio": {
            "data": "UklGRg==",
            "format": "wav"
          }
        },
        {
          "type": "file",
          "file": {
            "file_data": "data:application/pdf;base64,JVBERi0xLjQ=",
            "filename": "보고서.pdf"
          }
        },
        {
          "type": "text",
          "text": "&lt;documents&gt;\n&lt;document id=\"facts\"&gt;\n&lt;title&gt;사실표&lt;/title&gt;\n&lt;content&gt;서울=25도&lt;/content&gt;\n&lt;/document&gt;\n&lt;/documents&gt;"
        }
      ]
    },
    {
      "role": "assistant",
      "content": "서울은 25도다.\n\n&lt;references&gt;\n&lt;reference kind=\"url_citation\" uri=\"https://weather.example/seoul\" title=\"서울 관측\"/&gt;\n&lt;/references&gt;"
    },
    {
      "role": "user",
      "content": "한 줄로 다시 정리해줘."
    }
  ]
}</pre></td></tr>
<tr><th>Anthropic Messages</th><td><pre>{
  "messages": [
    {
      "role": "user",
      "content": [
        {
          "type": "text",
          "text": "다음 자료를 함께 분석해."
        },
        {
          "type": "image",
          "source": {
            "type": "base64",
            "media_type": "image/png",
            "data": "iVBORw0KGgo="
          }
        },
        {
          "type": "text",
          "text": "&lt;attachments&gt;\n&lt;audio media-type=\"audio/wav\" unavailable=\"true\"&gt;이 형식은 이 요청에 전달할 수 없다&lt;/audio&gt;\n&lt;/attachments&gt;"
        },
        {
          "type": "document",
          "source": {
            "type": "base64",
            "media_type": "application/pdf",
            "data": "JVBERi0xLjQ="
          },
          "title": "보고서.pdf",
          "citations": {
            "enabled": true
          }
        },
        {
          "type": "document",
          "source": {
            "type": "text",
            "media_type": "text/plain",
            "data": "서울=25도"
          },
          "title": "사실표",
          "citations": {
            "enabled": true
          }
        }
      ]
    },
    {
      "role": "assistant",
      "content": "&lt;attachments&gt;\n&lt;audio unavailable=\"true\"&gt;이 형식은 이 요청에 전달할 수 없다&lt;/audio&gt;\n&lt;/attachments&gt;\n\n서울은 25도다.\n\n&lt;references&gt;\n&lt;reference kind=\"url_citation\" uri=\"https://weather.example/seoul\" title=\"서울 관측\"/&gt;\n&lt;/references&gt;"
    },
    {
      "role": "user",
      "content": "한 줄로 다시 정리해줘."
    }
  ]
}</pre></td></tr>
<tr><th>OpenAI Responses (원 벤더 복원)</th><td><pre>{
  "input": [
    {
      "type": "message",
      "role": "user",
      "content": [
        {
          "type": "input_text",
          "text": "다음 자료를 함께 분석해."
        },
        {
          "type": "input_image",
          "image_url": "data:image/png;base64,iVBORw0KGgo="
        },
        {
          "type": "input_text",
          "text": "&lt;attachments&gt;\n&lt;audio media-type=\"audio/wav\" unavailable=\"true\"&gt;이 형식은 이 요청에 전달할 수 없다&lt;/audio&gt;\n&lt;/attachments&gt;"
        },
        {
          "type": "input_file",
          "file_data": "data:application/pdf;base64,JVBERi0xLjQ=",
          "filename": "보고서.pdf"
        },
        {
          "type": "input_text",
          "text": "&lt;documents&gt;\n&lt;document id=\"facts\"&gt;\n&lt;title&gt;사실표&lt;/title&gt;\n&lt;content&gt;서울=25도&lt;/content&gt;\n&lt;/document&gt;\n&lt;/documents&gt;"
        }
      ]
    },
    {
      "type": "reasoning",
      "id": "rs-rich-1",
      "summary": [
        {
          "type": "summary_text",
          "text": "웹 결과를 비교했다."
        }
      ],
      "encrypted_content": "responses-encrypted-reasoning"
    },
    {
      "type": "message",
      "id": "msg-responses-1",
      "role": "assistant",
      "status": "completed",
      "content": [
        {
          "type": "output_text",
          "text": "서울은 25도다.",
          "annotations": [
            {
              "type": "url_citation",
              "start_index": 0,
              "end_index": 2,
              "url": "https://weather.example/seoul",
              "title": "서울 관측"
            }
          ]
        }
      ]
    },
    {
      "type": "web_search_call",
      "id": "ws-rich-1",
      "status": "completed",
      "action": {
        "type": "search",
        "query": "서울 현재 기온"
      }
    },
    {
      "type": "code_interpreter_call",
      "id": "ci-rich-1",
      "status": "completed",
      "code": "print(25)",
      "outputs": [
        {
          "type": "logs",
          "logs": "25\n"
        }
      ]
    },
    {
      "type": "message",
      "role": "user",
      "content": [
        {
          "type": "input_text",
          "text": "한 줄로 다시 정리해줘."
        }
      ]
    }
  ]
}</pre></td></tr>
<tr><th>Gemini GenerateContent</th><td><pre>{
  "contents": [
    {
      "role": "user",
      "parts": [
        {
          "text": "다음 자료를 함께 분석해."
        },
        {
          "inlineData": {
            "mimeType": "image/png",
            "data": "iVBORw0KGgo="
          }
        },
        {
          "inlineData": {
            "mimeType": "audio/wav",
            "data": "UklGRg=="
          }
        },
        {
          "inlineData": {
            "mimeType": "application/pdf",
            "data": "JVBERi0xLjQ="
          }
        },
        {
          "text": "&lt;documents&gt;\n&lt;document id=\"facts\"&gt;\n&lt;title&gt;사실표&lt;/title&gt;\n&lt;content&gt;서울=25도&lt;/content&gt;\n&lt;/document&gt;\n&lt;/documents&gt;"
        }
      ]
    },
    {
      "role": "model",
      "parts": [
        {
          "text": "서울은 25도다.\n\n&lt;references&gt;\n&lt;reference kind=\"url_citation\" uri=\"https://weather.example/seoul\" title=\"서울 관측\"/&gt;\n&lt;/references&gt;"
        },
        {
          "inlineData": {
            "mimeType": "audio/wav",
            "data": "UklGRg=="
          }
        }
      ]
    },
    {
      "role": "user",
      "parts": [
        {
          "text": "한 줄로 다시 정리해줘."
        }
      ]
    }
  ]
}</pre></td></tr>
</table>

### 5.4. Gemini GenerateContent

포함 요소: thought signatures, image/PDF, code execution, citation/grounding metadata.

<table>
<tr><th>수신 raw event</th><td><pre>[
  {
    "responseId": "gemini-rich-1",
    "modelVersion": "gemini-model",
    "candidates": [
      {
        "content": {
          "role": "model",
          "parts": [
            {
              "text": "자료를 함께 검토한다.",
              "thought": true,
              "thoughtSignature": "gemini-rich-thought"
            },
            {
              "text": "서울은 25도다.",
              "thoughtSignature": "gemini-rich-text"
            },
            {
              "inlineData": {
                "mimeType": "image/png",
                "data": "iVBORw0KGgo="
              }
            },
            {
              "fileData": {
                "mimeType": "application/pdf",
                "fileUri": "gs://example/result.pdf"
              }
            },
            {
              "executableCode": {
                "language": "PYTHON",
                "code": "print(25)"
              }
            },
            {
              "codeExecutionResult": {
                "outcome": "OUTCOME_OK",
                "output": "25\n"
              }
            }
          ]
        },
        "finishReason": "STOP",
        "citationMetadata": {
          "citationSources": [
            {
              "startIndex": 0,
              "endIndex": 2,
              "uri": "https://weather.example/seoul",
              "license": "example"
            }
          ]
        },
        "groundingMetadata": {
          "webSearchQueries": [
            "서울 현재 기온"
          ],
          "groundingChunks": [
            {
              "web": {
                "uri": "https://weather.example/seoul",
                "title": "서울 관측"
              }
            }
          ],
          "groundingSupports": [
            {
              "segment": {
                "text": "서울은 25도다.",
                "startIndex": 0,
                "endIndex": 9
              },
              "groundingChunkIndices": [
                0
              ],
              "confidenceScores": [
                0.98
              ]
            }
          ]
        },
        "urlContextMetadata": {
          "urlMetadata": [
            {
              "retrievedUrl": "https://weather.example/seoul",
              "urlRetrievalStatus": "URL_RETRIEVAL_STATUS_SUCCESS"
            }
          ]
        }
      }
    ],
    "usageMetadata": {
      "promptTokenCount": 18,
      "candidatesTokenCount": 32
    }
  }
]</pre></td></tr>
<tr><th>정규화된 HubResponse</th><td><pre>{
  "id": "gemini-rich-1",
  "model": "gemini-model",
  "role": "assistant",
  "content": [
    {
      "type": "thinking",
      "index": 0,
      "source": "generate_content",
      "native": {
        "text": "자료를 함께 검토한다.",
        "thought": true,
        "thoughtSignature": "gemini-rich-thought"
      },
      "thinking": "자료를 함께 검토한다.",
      "signature": "gemini-rich-thought"
    },
    {
      "type": "text",
      "index": 1,
      "source": "generate_content",
      "native": {
        "text": "서울은 25도다.",
        "thoughtSignature": "gemini-rich-text"
      },
      "text": "서울은 25도다.",
      "signature": "gemini-rich-text",
      "citations": []
    },
    {
      "type": "image",
      "index": 2,
      "source": "generate_content",
      "native": {
        "inlineData": {
          "mimeType": "image/png",
          "data": "iVBORw0KGgo="
        }
      },
      "media_type": "image/png",
      "data": "iVBORw0KGgo=",
      "serialize": false
    },
    {
      "type": "document",
      "index": 3,
      "source": "generate_content",
      "native": {
        "fileData": {
          "mimeType": "application/pdf",
          "fileUri": "gs://example/result.pdf"
        }
      },
      "serialize": false,
      "id": "",
      "text": "",
      "uri": "gs://example/result.pdf",
      "media_type": "application/pdf",
      "citations_enabled": true
    },
    {
      "type": "server_tool",
      "index": 4,
      "source": "generate_content",
      "native": {
        "executableCode": {
          "language": "PYTHON",
          "code": "print(25)"
        }
      },
      "id": "",
      "name": "executableCode",
      "input_json": "{\"language\": \"PYTHON\", \"code\": \"print(25)\"}",
      "output": "",
      "raw": {
        "language": "PYTHON",
        "code": "print(25)"
      }
    },
    {
      "type": "server_tool",
      "index": 5,
      "source": "generate_content",
      "native": {
        "codeExecutionResult": {
          "outcome": "OUTCOME_OK",
          "output": "25\n"
        }
      },
      "id": "",
      "name": "codeExecutionResult",
      "input_json": "",
      "output": "25\n",
      "raw": {
        "outcome": "OUTCOME_OK",
        "output": "25\n"
      }
    },
    {
      "type": "annotation",
      "source": "generate_content",
      "native": {
        "startIndex": 0,
        "endIndex": 2,
        "uri": "https://weather.example/seoul",
        "license": "example"
      },
      "annotation_index": 0,
      "target_index": 1,
      "kind": "citation_source",
      "id": "https://weather.example/seoul",
      "start_index": 0,
      "end_index": 2,
      "uri": "https://weather.example/seoul"
    },
    {
      "type": "grounding",
      "source": "generate_content",
      "native": {
        "webSearchQueries": [
          "서울 현재 기온"
        ],
        "groundingChunks": [
          {
            "web": {
              "uri": "https://weather.example/seoul",
              "title": "서울 관측"
            }
          }
        ],
        "groundingSupports": [
          {
            "segment": {
              "text": "서울은 25도다.",
              "startIndex": 0,
              "endIndex": 9
            },
            "groundingChunkIndices": [
              0
            ],
            "confidenceScores": [
              0.98
            ]
          }
        ]
      },
      "candidate_index": 0,
      "sources": [
        {
          "index": 0,
          "kind": "web",
          "uri": "https://weather.example/seoul",
          "title": "서울 관측",
          "native": {
            "web": {
              "uri": "https://weather.example/seoul",
              "title": "서울 관측"
            }
          }
        }
      ],
      "supports": [
        {
          "index": 0,
          "text": "서울은 25도다.",
          "start_index": 0,
          "end_index": 9,
          "source_indices": [
            0
          ],
          "confidence_scores": [
            0.98
          ],
          "native": {
            "segment": {
              "text": "서울은 25도다.",
              "startIndex": 0,
              "endIndex": 9
            },
            "groundingChunkIndices": [
              0
            ],
            "confidenceScores": [
              0.98
            ]
          }
        }
      ],
      "search_queries": [
        "서울 현재 기온"
      ]
    },
    {
      "type": "server_tool",
      "source": "generate_content",
      "native": {},
      "id": "",
      "name": "urlContextMetadata",
      "input_json": "",
      "output": "",
      "raw": {
        "urlMetadata": [
          {
            "retrievedUrl": "https://weather.example/seoul",
            "urlRetrievalStatus": "URL_RETRIEVAL_STATUS_SUCCESS"
          }
        ]
      }
    }
  ],
  "stop_reason": "end_turn",
  "usage": {
    "input_tokens": 18,
    "output_tokens": 32
  }
}</pre></td></tr>
</table>

#### 네 벤더의 다음 요청 이력으로 변환

<table>
<tr><th>대상</th><th>실제 request conversation</th></tr>
<tr><th>Chat Completions</th><td><pre>{
  "messages": [
    {
      "role": "user",
      "content": [
        {
          "type": "text",
          "text": "다음 자료를 함께 분석해."
        },
        {
          "type": "image_url",
          "image_url": {
            "url": "data:image/png;base64,iVBORw0KGgo=",
            "detail": "low"
          }
        },
        {
          "type": "input_audio",
          "input_audio": {
            "data": "UklGRg==",
            "format": "wav"
          }
        },
        {
          "type": "file",
          "file": {
            "file_data": "data:application/pdf;base64,JVBERi0xLjQ=",
            "filename": "보고서.pdf"
          }
        },
        {
          "type": "text",
          "text": "&lt;documents&gt;\n&lt;document id=\"facts\"&gt;\n&lt;title&gt;사실표&lt;/title&gt;\n&lt;content&gt;서울=25도&lt;/content&gt;\n&lt;/document&gt;\n&lt;/documents&gt;"
        }
      ]
    },
    {
      "role": "assistant",
      "content": "서울은 25도다.",
      "tool_calls": [
        {
          "id": "executableCode_4",
          "type": "function",
          "function": {
            "name": "gemini_executableCode",
            "arguments": "{\"language\": \"PYTHON\", \"code\": \"print(25)\"}"
          }
        }
      ]
    },
    {
      "role": "tool",
      "tool_call_id": "executableCode_4",
      "content": ""
    },
    {
      "role": "assistant",
      "content": null,
      "tool_calls": [
        {
          "id": "codeExecutionResult_5",
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
      "tool_call_id": "codeExecutionResult_5",
      "content": "25\n"
    },
    {
      "role": "assistant",
      "content": "&lt;references&gt;\n&lt;reference kind=\"citation_source\" uri=\"https://weather.example/seoul\"/&gt;\n&lt;source index=\"0\" uri=\"https://weather.example/seoul\" title=\"서울 관측\"/&gt;\n&lt;support sources=\"0\" start=\"0\" end=\"9\"&gt;서울은 25도다.&lt;/support&gt;\n&lt;query&gt;서울 현재 기온&lt;/query&gt;\n&lt;/references&gt;"
    },
    {
      "role": "user",
      "content": "한 줄로 다시 정리해줘."
    }
  ]
}</pre></td></tr>
<tr><th>Anthropic Messages</th><td><pre>{
  "messages": [
    {
      "role": "user",
      "content": [
        {
          "type": "text",
          "text": "다음 자료를 함께 분석해."
        },
        {
          "type": "image",
          "source": {
            "type": "base64",
            "media_type": "image/png",
            "data": "iVBORw0KGgo="
          }
        },
        {
          "type": "text",
          "text": "&lt;attachments&gt;\n&lt;audio media-type=\"audio/wav\" unavailable=\"true\"&gt;이 형식은 이 요청에 전달할 수 없다&lt;/audio&gt;\n&lt;/attachments&gt;"
        },
        {
          "type": "document",
          "source": {
            "type": "base64",
            "media_type": "application/pdf",
            "data": "JVBERi0xLjQ="
          },
          "title": "보고서.pdf",
          "citations": {
            "enabled": true
          }
        },
        {
          "type": "document",
          "source": {
            "type": "text",
            "media_type": "text/plain",
            "data": "서울=25도"
          },
          "title": "사실표",
          "citations": {
            "enabled": true
          }
        }
      ]
    },
    {
      "role": "assistant",
      "content": [
        {
          "type": "text",
          "text": "서울은 25도다."
        },
        {
          "type": "image",
          "source": {
            "type": "base64",
            "media_type": "image/png",
            "data": "iVBORw0KGgo="
          }
        },
        {
          "type": "tool_use",
          "id": "executableCode_4",
          "name": "gemini_executableCode",
          "input": {
            "language": "PYTHON",
            "code": "print(25)"
          }
        }
      ]
    },
    {
      "role": "user",
      "content": [
        {
          "type": "tool_result",
          "tool_use_id": "executableCode_4",
          "content": ""
        }
      ]
    },
    {
      "role": "assistant",
      "content": [
        {
          "type": "tool_use",
          "id": "codeExecutionResult_5",
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
          "tool_use_id": "codeExecutionResult_5",
          "content": "25\n"
        }
      ]
    },
    {
      "role": "assistant",
      "content": "&lt;references&gt;\n&lt;reference kind=\"citation_source\" uri=\"https://weather.example/seoul\"/&gt;\n&lt;source index=\"0\" uri=\"https://weather.example/seoul\" title=\"서울 관측\"/&gt;\n&lt;support sources=\"0\" start=\"0\" end=\"9\"&gt;서울은 25도다.&lt;/support&gt;\n&lt;query&gt;서울 현재 기온&lt;/query&gt;\n&lt;/references&gt;"
    },
    {
      "role": "user",
      "content": "한 줄로 다시 정리해줘."
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
          "text": "다음 자료를 함께 분석해."
        },
        {
          "type": "input_image",
          "image_url": "data:image/png;base64,iVBORw0KGgo="
        },
        {
          "type": "input_text",
          "text": "&lt;attachments&gt;\n&lt;audio media-type=\"audio/wav\" unavailable=\"true\"&gt;이 형식은 이 요청에 전달할 수 없다&lt;/audio&gt;\n&lt;/attachments&gt;"
        },
        {
          "type": "input_file",
          "file_data": "data:application/pdf;base64,JVBERi0xLjQ=",
          "filename": "보고서.pdf"
        },
        {
          "type": "input_text",
          "text": "&lt;documents&gt;\n&lt;document id=\"facts\"&gt;\n&lt;title&gt;사실표&lt;/title&gt;\n&lt;content&gt;서울=25도&lt;/content&gt;\n&lt;/document&gt;\n&lt;/documents&gt;"
        }
      ]
    },
    {
      "type": "message",
      "role": "assistant",
      "content": "&lt;documents&gt;\n&lt;document id=\"\"&gt;\n&lt;content media-type=\"application/pdf\"&gt;gs://example/result.pdf&lt;/content&gt;\n&lt;/document&gt;\n&lt;/documents&gt;\n\n서울은 25도다."
    },
    {
      "type": "function_call",
      "call_id": "executableCode_4",
      "name": "gemini_executableCode",
      "arguments": "{\"language\": \"PYTHON\", \"code\": \"print(25)\"}"
    },
    {
      "type": "function_call_output",
      "call_id": "executableCode_4",
      "output": ""
    },
    {
      "type": "function_call",
      "call_id": "codeExecutionResult_5",
      "name": "gemini_codeExecutionResult",
      "arguments": "{}"
    },
    {
      "type": "function_call_output",
      "call_id": "codeExecutionResult_5",
      "output": "25\n"
    },
    {
      "type": "message",
      "role": "assistant",
      "content": "&lt;references&gt;\n&lt;reference kind=\"citation_source\" uri=\"https://weather.example/seoul\"/&gt;\n&lt;source index=\"0\" uri=\"https://weather.example/seoul\" title=\"서울 관측\"/&gt;\n&lt;support sources=\"0\" start=\"0\" end=\"9\"&gt;서울은 25도다.&lt;/support&gt;\n&lt;query&gt;서울 현재 기온&lt;/query&gt;\n&lt;/references&gt;"
    },
    {
      "type": "message",
      "role": "user",
      "content": [
        {
          "type": "input_text",
          "text": "한 줄로 다시 정리해줘."
        }
      ]
    }
  ]
}</pre></td></tr>
<tr><th>Gemini GenerateContent (원 벤더 복원)</th><td><pre>{
  "rejected": "vendor file URIs are not supported; use an HTTP(S) URL or inline base64 data"
}</pre></td></tr>
</table>

## 6. 내장 도구의 명시적 활성화

Bridge는 내장 도구를 자동 등록하지 않는다. `tools`를 생략한 네 요청에는 해당 필드가 없고, 세션 상태가 필요 없는 web search도 호출자가 matching vendor의 `ToolDefinition.native(...)`를 전달한 요청에서만 활성화된다.

<table>
<tr><th>Chat Completions: tools 생략</th><td><pre>{
  "messages": [
    {
      "role": "user",
      "content": "오늘 자료를 웹에서 찾아줘."
    }
  ]
}</pre></td></tr>
<tr><th>Anthropic Messages: tools 생략</th><td><pre>{
  "messages": [
    {
      "role": "user",
      "content": "오늘 자료를 웹에서 찾아줘."
    }
  ]
}</pre></td></tr>
<tr><th>OpenAI Responses: tools 생략</th><td><pre>{
  "input": [
    {
      "type": "message",
      "role": "user",
      "content": [
        {
          "type": "input_text",
          "text": "오늘 자료를 웹에서 찾아줘."
        }
      ]
    }
  ]
}</pre></td></tr>
<tr><th>Gemini GenerateContent: tools 생략</th><td><pre>{
  "contents": [
    {
      "role": "user",
      "parts": [
        {
          "text": "오늘 자료를 웹에서 찾아줘."
        }
      ]
    }
  ]
}</pre></td></tr>
<tr><th>Anthropic Messages: web search 명시</th><td><pre>{
  "messages": [
    {
      "role": "user",
      "content": "오늘 자료를 웹에서 찾아줘."
    }
  ],
  "tools": [
    {
      "type": "web_search_20250305",
      "name": "web_search",
      "max_uses": 1
    }
  ]
}</pre></td></tr>
<tr><th>OpenAI Responses: web search 명시</th><td><pre>{
  "input": [
    {
      "type": "message",
      "role": "user",
      "content": [
        {
          "type": "input_text",
          "text": "오늘 자료를 웹에서 찾아줘."
        }
      ]
    }
  ],
  "tools": [
    {
      "type": "web_search_preview"
    }
  ]
}</pre></td></tr>
<tr><th>Gemini GenerateContent: web search 명시</th><td><pre>{
  "contents": [
    {
      "role": "user",
      "parts": [
        {
          "text": "오늘 자료를 웹에서 찾아줘."
        }
      ]
    }
  ],
  "tools": [
    {
      "googleSearch": {}
    }
  ]
}</pre></td></tr>
</table>

## 7. Hyperparameters의 API별 선택

같은 옵션 객체를 네 request builder에 넣고, 대화·도구 정의를 제외한 실제 wire 파라미터만 표시했다. 대상이 소유한 필드만 남고 나머지는 빠진다. 필드 사이 전이는 없으므로 출력 예산과 stop은 API별 필드를 각각 설정했다.

<table>
<tr><th>Chat Completions</th><td><pre>{
  "temperature": 0.2,
  "top_p": 0.9,
  "top_k": 20,
  "seed": 7,
  "presence_penalty": 0.1,
  "frequency_penalty": 0.2,
  "parallel_tool_calls": false,
  "max_completion_tokens": 64,
  "stop": [
    "&lt;END&gt;"
  ],
  "reasoning_effort": "low",
  "chat_template_kwargs": {
    "enable_thinking": false
  },
  "tool_choice": {
    "type": "function",
    "function": {
      "name": "lookup"
    }
  },
  "response_format": {
    "type": "json_schema",
    "json_schema": {
      "name": "answer",
      "schema": {
        "type": "object",
        "properties": {
          "answer": {
            "type": "string"
          }
        }
      },
      "strict": true
    }
  }
}</pre></td></tr>
<tr><th>Anthropic Messages</th><td><pre>{
  "max_tokens": 64,
  "temperature": 0.2,
  "top_p": 0.9,
  "top_k": 20,
  "stop_sequences": [
    "&lt;END&gt;"
  ],
  "service_tier": "standard_only",
  "inference_geo": "us",
  "tool_choice": {
    "type": "tool",
    "name": "lookup",
    "disable_parallel_tool_use": true
  },
  "output_config": {
    "format": {
      "type": "json_schema",
      "schema": {
        "type": "object",
        "properties": {
          "answer": {
            "type": "string"
          }
        }
      }
    }
  }
}</pre></td></tr>
<tr><th>OpenAI Responses</th><td><pre>{
  "temperature": 0.2,
  "top_p": 0.9,
  "max_output_tokens": 64,
  "parallel_tool_calls": false,
  "include": [
    "reasoning.encrypted_content"
  ],
  "service_tier": "auto",
  "tool_choice": {
    "type": "function",
    "name": "lookup"
  },
  "text": {
    "format": {
      "type": "json_schema",
      "name": "answer",
      "schema": {
        "type": "object",
        "properties": {
          "answer": {
            "type": "string"
          }
        }
      },
      "strict": true
    }
  }
}</pre></td></tr>
<tr><th>Gemini GenerateContent</th><td><pre>{
  "serviceTier": "PRIORITY",
  "toolConfig": {
    "functionCallingConfig": {
      "mode": "ANY",
      "allowedFunctionNames": [
        "lookup"
      ]
    }
  },
  "generationConfig": {
    "temperature": 0.2,
    "topP": 0.9,
    "topK": 20,
    "seed": 7,
    "presencePenalty": 0.1,
    "frequencyPenalty": 0.2,
    "maxOutputTokens": 64,
    "stopSequences": [
      "&lt;END&gt;"
    ],
    "responseMimeType": "application/json",
    "responseJsonSchema": {
      "type": "object",
      "properties": {
        "answer": {
          "type": "string"
        }
      }
    }
  }
}</pre></td></tr>
</table>

## 8. 견본 범위

이 문서는 core Hub content 계열 전부(text, thinking, tool use/result, image, audio, document, nested citation, annotation, grounding, server tool, vendor fallback)와 요청 Hyperparameters의 변환 정책을 실제 adapter 또는 해당 adapter의 회귀 테스트에 통과시킨다. 각 서버 도구의 모든 버전 문자열을 반복하지는 않고 실행 주체와 변환 정책이 같은 계열별 대표 wire payload를 사용한다. 전체 subtype 목록은 [지원표](Support-Matrix.md)에 있다.

---

# 사용자 정의 XML-like vocabulary 실행 예시

이 결과는 `examples/custom_vocabulary.py`의 `BadgeBlock`/`BadgeVocabulary`를 실제로 
등록한 뒤, 태그가 세 delta로 잘린 Chat Completions 응답을 구조화된 JSON block으로 
올리고 네 요청 형식의 text content로 다시 내린 결과다.

`notice`는 입력 alias이고 직렬화는 canonical `badge` 태그만 사용한다. 속성과 본문은 
escape/unescape하며, 하나의 wire text를 여러 의미 block으로 나눌 때는 파생 block을 
unindexed로 만들어 최종 병합에서 순서를 보존한다.

<table>
<tr><th>분할된 Chat delta</th><td><pre>[
  {
    "choices": [
      {
        "delta": {
          "role": "assistant",
          "content": "앞 &lt;ba"
        }
      }
    ]
  },
  {
    "choices": [
      {
        "delta": {
          "content": "dge level=\"warning\"&gt;점검 &amp;amp; 확인"
        }
      }
    ]
  },
  {
    "choices": [
      {
        "delta": {
          "content": "&lt;/badge&gt; 뒤"
        },
        "finish_reason": "stop"
      }
    ]
  }
]</pre></td></tr>
<tr><th>올림 결과 HubResponse</th><td><pre>{
  "role": "assistant",
  "content": [
    {
      "type": "text",
      "source": "chat_completions",
      "native": {},
      "text": "앞 ",
      "citations": []
    },
    {
      "type": "badge",
      "source": "chat_completions",
      "native": {},
      "level": "warning",
      "text": "점검 &amp; 확인"
    },
    {
      "type": "text",
      "source": "chat_completions",
      "native": {},
      "text": " 뒤",
      "citations": []
    }
  ],
  "stop_reason": "end_turn"
}</pre></td></tr>
<tr><th>저장 JSON 재검증</th><td><pre>{
  "role": "assistant",
  "content": [
    {
      "type": "badge",
      "native": {},
      "level": "warning",
      "text": "저장된 블록"
    }
  ]
}</pre></td></tr>
<tr><th>Chat Completions 요청 직렬화</th><td><pre>{
  "messages": [
    {
      "role": "assistant",
      "content": [
        {
          "type": "text",
          "text": "앞 "
        },
        {
          "type": "text",
          "text": "&lt;badge level=\"warning\"&gt;점검 &amp;amp; 확인&lt;/badge&gt;"
        },
        {
          "type": "text",
          "text": " 뒤"
        }
      ]
    }
  ]
}</pre></td></tr>
<tr><th>Anthropic Messages 요청 직렬화</th><td><pre>{
  "messages": [
    {
      "role": "assistant",
      "content": [
        {
          "type": "text",
          "text": "앞 "
        },
        {
          "type": "text",
          "text": "&lt;badge level=\"warning\"&gt;점검 &amp;amp; 확인&lt;/badge&gt;"
        },
        {
          "type": "text",
          "text": " 뒤"
        }
      ]
    }
  ]
}</pre></td></tr>
<tr><th>OpenAI Responses 요청 직렬화</th><td><pre>{
  "input": [
    {
      "type": "message",
      "role": "assistant",
      "content": "앞 &lt;badge level=\"warning\"&gt;점검 &amp;amp; 확인&lt;/badge&gt; 뒤"
    }
  ]
}</pre></td></tr>
<tr><th>Gemini GenerateContent 요청 직렬화</th><td><pre>{
  "contents": [
    {
      "role": "model",
      "parts": [
        {
          "text": "앞 "
        },
        {
          "text": "&lt;badge level=\"warning\"&gt;점검 &amp;amp; 확인&lt;/badge&gt;"
        },
        {
          "text": " 뒤"
        }
      ]
    }
  ]
}</pre></td></tr>
</table>
