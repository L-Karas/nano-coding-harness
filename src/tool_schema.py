"""
Tool Definitions

The model sees tool schemas; Python executes handlers. S20 keeps both tables
explicit so every added capability is visible in one place.
"""

BUILTIN_TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "bash",
            "description": "Execute a bash command.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "The bash command to execute."},
                    "run_in_background": {"type": "boolean",
                                          "description": "Set to true to run the command in the background."},
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a file from the filesystem.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to the file to read."},
                    "limit": {"type": "integer", "description": "Max lines to read."},
                    "offset": {"type": "integer", "description": "Line offset to start reading from."},
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
                    "path": {"type": "string", "description": "Path to the file to write."},
                    "content": {"type": "string", "description": "Content to write to the file."},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "Find and replace text in a file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to the file to edit."},
                    "old_text": {"type": "string", "description": "The exact text to find and replace."},
                    "new_text": {"type": "string", "description": "The replacement text."},
                },
                "required": ["path", "old_text", "new_text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "glob",
            "description": "Find files matching a glob pattern.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string",
                                "description": "The glob pattern to match files against (e.g. '**/*.py')."},
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "todo_write",
            "description": "Create and manage a task list for the current session.",
            "parameters": {
                "type": "object",
                "properties": {
                    "todos": {
                        "type": "array",
                        "description": "List of todo items to create or update.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "content": {"type": "string", "description": "The todo item description."},
                                "status": {
                                    "type": "string",
                                    "description": "The status of the todo item.",
                                    "enum": ["pending", "in_progress", "completed"],
                                },
                            },
                            "required": ["content", "status"],
                        },
                    },
                },
                "required": ["todos"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "task",
            "description": "Launch a focused subagent. Returns only its final summary.",
            "parameters": {
                "type": "object",
                "properties": {
                    "description": {"type": "string",
                                    "description": "The task description for the subagent to complete."},
                },
                "required": ["description"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "load_skill",
            "description": "Load the full content of a skill by name.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "The name of the skill to load."},
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "compact",
            "description": "Summarize earlier conversation and continue with compacted context.",
            "parameters": {
                "type": "object",
                "properties": {
                    "focus": {"type": "string",
                              "description": "What to focus on when summarizing (e.g. 'current goal', 'key findings')."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_task",
            "description": "Create a task for the task system.",
            "parameters": {
                "type": "object",
                "properties": {
                    "subject": {"type": "string", "description": "Brief title of the task."},
                    "description": {"type": "string", "description": "Detailed description of what the task involves."},
                    "blockedBy": {
                        "type": "array",
                        "description": "List of task IDs that must be completed before this task can start.",
                        "items": {
                            "type": "string",
                            "description": "A task ID that blocks this task.",
                        },
                    },
                },
                "required": ["subject"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_tasks",
            "description": "List all tasks with their status, owner, and worktree.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_task",
            "description": "Get full details of a task by its ID.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "The task ID to retrieve details for."},
                },
                "required": ["task_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "claim_task",
            "description": "Claim a pending task and start working on it.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "The task ID to claim."},
                },
                "required": ["task_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "complete_task",
            "description": "Complete an in-progress task.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "The task ID to complete."},
                },
                "required": ["task_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "schedule_cron",
            "description": ("Schedule a cron job. cron is 5-field: min hour dom "
                            "month dow. For one-shot reminders, compute the target "
                            "minute and set recurring=false."),
            "parameters": {
                "type": "object",
                "properties": {
                    "cron_expression": {"type": "string",
                                        "description": "5-field cron expression (minute hour day-of-month month day-of-week)."},
                    "prompt": {"type": "string", "description": "The prompt to enqueue at each fire time."},
                    "recurring": {"type": "boolean",
                                  "description": "Whether the job repeats on every cron match (default: true)."},
                    "durable": {"type": "boolean",
                                "description": "Whether the job persists across sessions (default: true)."},
                },
                "required": ["cron_expression", "prompt"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_crons",
            "description": "List all registered cron jobs.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cancel_cron",
            "description": "Cancel a cron job by ID.",
            "parameters": {
                "type": "object",
                "properties": {
                    "job_id": {"type": "string", "description": "The ID of the cron job to cancel."},
                },
                "required": ["job_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "spawn_teammate",
            "description": "Spawn an autonomous teammate agent.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Unique name for the teammate."},
                    "role": {"type": "string",
                             "description": "The role or expertise of the teammate (e.g. 'code reviewer')."},
                    "prompt": {"type": "string", "description": "The initial task or instructions for the teammate."},
                },
                "required": ["name", "role", "prompt"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_message",
            "description": "Send a message to another agent.",
            "parameters": {
                "type": "object",
                "properties": {
                    "to_agent": {"type": "string", "description": "The name of the agent to send the message to."},
                    "content": {"type": "string", "description": "The message content to send."},
                },
                "required": ["to_agent", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_inbox",
            "description": "Check inbox for messages and protocol responses.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "request_shutdown",
            "description": "Request a teammate to shut down.",
            "parameters": {
                "type": "object",
                "properties": {
                    "teammate": {"type": "string", "description": "The name of the teammate to request shutdown from."},
                },
                "required": ["teammate"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "request_plan",
            "description": "Ask a teammate to submit a plan for review.",
            "parameters": {
                "type": "object",
                "properties": {
                    "teammate": {"type": "string", "description": "The name of the teammate to request a plan from."},
                    "task": {"type": "string", "description": "The task description for which a plan is needed."},
                },
                "required": ["teammate", "task"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "review_plan",
            "description": "Approve or reject a submitted plan.",
            "parameters": {
                "type": "object",
                "properties": {
                    "request_id": {"type": "string", "description": "The request ID of the plan to review."},
                    "approve": {"type": "boolean",
                                "description": "Whether to approve (true) or reject (false) the plan."},
                    "feedback": {"type": "string", "description": "Optional feedback when rejecting a plan."},
                },
                "required": ["request_id", "approve"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_worktree",
            "description": "Create an isolated git worktree for a task.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Name for the new worktree."},
                    "task_id": {"type": "string", "description": "Optional task ID to associate with the worktree."},
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remove_worktree",
            "description": "Remove a worktree. Refuses if uncommitted changes exist.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "The name of the worktree to remove."},
                    "discard_changes": {"type": "boolean",
                                        "description": "Set to true to force removal even with uncommitted changes."},
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "keep_worktree",
            "description": "Keep a worktree for manual review instead of auto-removing it.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "The name of the worktree to keep."},
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "connect_mcp",
            "description": "Connect to an MCP server and discover its tools.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "The name of the MCP server to connect to."},
                },
                "required": ["name"],
            },
        },
    },
]
