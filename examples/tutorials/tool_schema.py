# Child gets all base tools except task (no recursive spawning)
BASE_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "bash",
            "description": "Run a shell command.",
            "parameters": {
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read file contents.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "limit": {"type": "integer"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write content to a file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    },
    # {
    #     "type": "function",
    #     "function": {
    #         "name": "edit_file",
    #         "description": "Replace exact text in a file once.",
    #         "parameters": {
    #             "type": "object",
    #             "properties": {
    #                 "path": {"type": "string"},
    #                 "old_text": {"type": "string"},
    #                 "new_text": {"type": "string"},
    #             },
    #             "required": ["path", "old_text", "new_text"],
    #         },
    #     },
    # },
]

PARENT_TOOLS = BASE_TOOLS + [
    {
        "type": "function",
        "function": {
            "name": "task",
            "description": "Spawn a subagent with fresh context. It shares the filesystem but not conversation history.",
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string"},
                    "description": {
                        "type": "string",
                        "description": "Short description of the task",
                    },
                },
                "required": ["prompt"],
            },
        },
    }
]

SKILLS_TOOLS = BASE_TOOLS + [
    {
        "type": "function",
        "function": {
            "name": "load_skill",
            "description": "Load the full body of a named skill into the current context.",
            "parameters": {
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
        },
    }
]

COMPACT_TOOLS = BASE_TOOLS + [
    {
        "type": "function",
        "function": {
            "name": "compact",
            "description": "Summarize earlier conversation so work can continue in a smaller context.",
            "parameters": {
                "type": "object",
                "properties": {
                    "focus": {"type": "string", "description": "What tasks You need to focus next."},
                },
            },
        },
    }
]

TASK_TOOLS = BASE_TOOLS + [
    {
        "type": "function",
        "function": {
            "name": "create_task",
            "description": "Create a new task with optional blockedBy dependencies.",
            "parameters": {
                "type": "object",
                "properties": {"subject": {"type": "string", "description": "任务主题"},
                               "description": {"type": "string", "description": "任务描述"},
                               "blockedBy": {"type": "array", "description": "任务前置任务",
                                             "items": {"type": "string"}}},
                "required": ["subject"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_tasks",
            "description": "List all tasks with status, owner, and dependencies.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_task",
            "description": "Get full details of a specific task by ID.",
            "parameters": {
                "type": "object",
                "properties": {"task_id": {"type": "string"}},
                "required": ["task_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "claim_task",
            "description": "Claim a pending task. Sets owner, changes status to in_progress.",
            "parameters": {
                "type": "object",
                "properties": {"task_id": {"type": "string"}},
                "required": ["task_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "complete_task",
            "description": "Complete an in-progress task. Reports unblocked downstream tasks.",
            "parameters": {
                "type": "object",
                "properties": {"task_id": {"type": "string"}},
                "required": ["task_id"],
            },
        },
    },
]
