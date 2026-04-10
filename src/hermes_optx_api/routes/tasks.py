"""Task orchestration routes — create, claim, execute, complete tasks via SpacetimeDB."""

import json
import time
import uuid
from enum import Enum
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

router = APIRouter()

# SpacetimeDB connection (Jetson edge node)
STDB_URL = "http://100.85.183.16:3000"
STDB_DB = "optx-cortex"
STDB_TIMEOUT = 8.0


class TaskStatus(str, Enum):
    open = "Open"
    claimed = "InProgress"
    pending_validation = "PendingValidation"
    completed = "Completed"
    failed = "Failed"
    cancelled = "Cancelled"


class TaskCreate(BaseModel):
    title: str
    description: str
    reward: float = 0.0
    capabilities: list[str] = Field(default_factory=list)
    validation_mode: str = "CreatorReview"
    priority: int = Field(default=5, ge=1, le=10)
    parent_task_id: Optional[str] = None  # For DAG workflows
    depends_on: list[str] = Field(default_factory=list)  # Task IDs this depends on
    assigned_agent: Optional[str] = None  # Pre-assign to specific agent
    gaze_required: bool = False  # Requires AARON gaze verification


class TaskClaim(BaseModel):
    task_id: str
    agent_name: str = "astrojoe"


class TaskComplete(BaseModel):
    task_id: str
    result: str
    agent_name: str = "astrojoe"


class TaskUpdate(BaseModel):
    status: Optional[TaskStatus] = None
    result: Optional[str] = None
    metadata: Optional[str] = None


# ---------------------------------------------------------------------------
# SpacetimeDB helpers
# ---------------------------------------------------------------------------

async def _stdb_sql(query: str) -> dict:
    """Execute SQL query against SpacetimeDB."""
    async with httpx.AsyncClient(timeout=STDB_TIMEOUT) as client:
        resp = await client.post(
            f"{STDB_URL}/v1/database/{STDB_DB}/sql",
            content=query,
            headers={"Content-Type": "text/plain"},
        )
        if resp.status_code == 200:
            return resp.json()
        return {"error": resp.text}


async def _stdb_call(reducer: str, args: list) -> dict:
    """Call a SpacetimeDB reducer."""
    async with httpx.AsyncClient(timeout=STDB_TIMEOUT) as client:
        resp = await client.post(
            f"{STDB_URL}/v1/database/{STDB_DB}/call/{reducer}",
            json=args,
            headers={"Content-Type": "application/json"},
        )
        if resp.status_code in (200, 204):
            return {"ok": True}
        return {"error": resp.text}


# ---------------------------------------------------------------------------
# Task CRUD
# ---------------------------------------------------------------------------

@router.post("/tasks")
async def create_task(task: TaskCreate):
    """Create a new task in SpacetimeDB."""
    task_id = str(uuid.uuid4())[:12]
    now = int(time.time())

    metadata = json.dumps({
        "capabilities": task.capabilities,
        "validation_mode": task.validation_mode,
        "priority": task.priority,
        "parent_task_id": task.parent_task_id,
        "depends_on": task.depends_on,
        "assigned_agent": task.assigned_agent,
        "gaze_required": task.gaze_required,
        "reward": task.reward,
        "created_at": now,
    })

    # Store as memory entry with category "task"
    result = await _stdb_call("store_memory", [
        "task",
        f"task:{task_id}",
        json.dumps({
            "title": task.title,
            "description": task.description,
            "status": TaskStatus.open.value,
            "result": None,
            "metadata": json.loads(metadata),
        }),
        "hermes-optx-api",
        task.assigned_agent or "unassigned",
        task.priority,
        "",
    ])

    if "error" in result:
        raise HTTPException(status_code=500, detail=result["error"])

    return {
        "task_id": task_id,
        "status": TaskStatus.open.value,
        "title": task.title,
        "priority": task.priority,
        "gaze_required": task.gaze_required,
    }


@router.get("/tasks")
async def list_tasks(
    status: Optional[str] = None,
    agent: Optional[str] = None,
    limit: int = 50,
):
    """List tasks from SpacetimeDB."""
    result = await _stdb_sql(
        "SELECT id, category, key, value, importance, owner, created_at "
        "FROM memory_entry WHERE category = 'task'"
    )

    if "error" in result:
        raise HTTPException(status_code=500, detail=result["error"])

    tasks = []
    rows = result if isinstance(result, list) else result.get("rows", [])
    for row in rows:
        try:
            value = row.get("value", "{}")
            data = json.loads(value) if isinstance(value, str) else value
            task_status = data.get("status", "Open")
            task_agent = data.get("metadata", {}).get("assigned_agent", "")

            if status and task_status != status:
                continue
            if agent and task_agent != agent:
                continue

            tasks.append({
                "task_id": row.get("key", "").replace("task:", ""),
                "title": data.get("title", ""),
                "description": data.get("description", ""),
                "status": task_status,
                "priority": row.get("importance", 5),
                "agent": row.get("owner", ""),
                "metadata": data.get("metadata", {}),
                "created_at": row.get("created_at", ""),
            })
        except (json.JSONDecodeError, AttributeError):
            continue

    # Sort by priority descending
    tasks.sort(key=lambda t: t.get("priority", 0), reverse=True)
    return {"tasks": tasks[:limit], "total": len(tasks)}


@router.get("/tasks/{task_id}")
async def get_task(task_id: str):
    """Get a specific task by ID."""
    result = await _stdb_sql(
        f"SELECT * FROM memory_entry WHERE key = 'task:{task_id}'"
    )

    if "error" in result:
        raise HTTPException(status_code=500, detail=result["error"])

    rows = result if isinstance(result, list) else result.get("rows", [])
    if not rows:
        raise HTTPException(status_code=404, detail="Task not found")

    row = rows[0]
    data = json.loads(row.get("value", "{}"))
    return {
        "task_id": task_id,
        "title": data.get("title", ""),
        "description": data.get("description", ""),
        "status": data.get("status", "Open"),
        "result": data.get("result"),
        "priority": row.get("importance", 5),
        "agent": row.get("owner", ""),
        "metadata": data.get("metadata", {}),
    }


@router.post("/tasks/{task_id}/claim")
async def claim_task(task_id: str, claim: TaskClaim):
    """Claim a task — assigns it to an agent."""
    # Get current task
    result = await _stdb_sql(
        f"SELECT * FROM memory_entry WHERE key = 'task:{task_id}'"
    )
    rows = result if isinstance(result, list) else result.get("rows", [])
    if not rows:
        raise HTTPException(status_code=404, detail="Task not found")

    row = rows[0]
    data = json.loads(row.get("value", "{}"))

    if data.get("status") != TaskStatus.open.value:
        raise HTTPException(status_code=409, detail=f"Task is {data.get('status')}, not Open")

    # Check if gaze verification required
    meta = data.get("metadata", {})
    if meta.get("gaze_required"):
        gaze_ok = await _verify_gaze_attestation(claim.agent_name)
        if not gaze_ok:
            raise HTTPException(
                status_code=403,
                detail="Gaze verification required — complete AARON session first",
            )

    # Update task status
    data["status"] = TaskStatus.claimed.value
    meta["assigned_agent"] = claim.agent_name
    meta["claimed_at"] = int(time.time())
    data["metadata"] = meta

    await _stdb_call("store_memory", [
        "task",
        f"task:{task_id}",
        json.dumps(data),
        "hermes-optx-api",
        claim.agent_name,
        row.get("importance", 5),
        "",
    ])

    return {
        "task_id": task_id,
        "status": TaskStatus.claimed.value,
        "agent": claim.agent_name,
    }


@router.post("/tasks/{task_id}/complete")
async def complete_task(task_id: str, completion: TaskComplete):
    """Complete a task with result data."""
    result = await _stdb_sql(
        f"SELECT * FROM memory_entry WHERE key = 'task:{task_id}'"
    )
    rows = result if isinstance(result, list) else result.get("rows", [])
    if not rows:
        raise HTTPException(status_code=404, detail="Task not found")

    row = rows[0]
    data = json.loads(row.get("value", "{}"))

    if data.get("status") != TaskStatus.claimed.value:
        raise HTTPException(
            status_code=409,
            detail=f"Task is {data.get('status')}, must be InProgress to complete",
        )

    data["status"] = TaskStatus.completed.value
    data["result"] = completion.result
    meta = data.get("metadata", {})
    meta["completed_at"] = int(time.time())
    meta["completed_by"] = completion.agent_name
    data["metadata"] = meta

    await _stdb_call("store_memory", [
        "task",
        f"task:{task_id}",
        json.dumps(data),
        "hermes-optx-api",
        completion.agent_name,
        row.get("importance", 5),
        "",
    ])

    # Check if this unblocks any dependent tasks
    await _check_dag_dependencies(task_id)

    return {
        "task_id": task_id,
        "status": TaskStatus.completed.value,
        "result": completion.result,
    }


@router.delete("/tasks/{task_id}")
async def cancel_task(task_id: str):
    """Cancel a task."""
    result = await _stdb_sql(
        f"SELECT * FROM memory_entry WHERE key = 'task:{task_id}'"
    )
    rows = result if isinstance(result, list) else result.get("rows", [])
    if not rows:
        raise HTTPException(status_code=404, detail="Task not found")

    row = rows[0]
    data = json.loads(row.get("value", "{}"))
    data["status"] = TaskStatus.cancelled.value
    meta = data.get("metadata", {})
    meta["cancelled_at"] = int(time.time())
    data["metadata"] = meta

    await _stdb_call("store_memory", [
        "task",
        f"task:{task_id}",
        json.dumps(data),
        "hermes-optx-api",
        row.get("owner", ""),
        row.get("importance", 5),
        "",
    ])

    return {"task_id": task_id, "status": TaskStatus.cancelled.value}


# ---------------------------------------------------------------------------
# DAG Workflow helpers
# ---------------------------------------------------------------------------

async def _check_dag_dependencies(completed_task_id: str):
    """Check if completing this task unblocks dependent tasks."""
    all_tasks = await _stdb_sql(
        "SELECT key, value FROM memory_entry WHERE category = 'task'"
    )
    rows = all_tasks if isinstance(all_tasks, list) else all_tasks.get("rows", [])

    for row in rows:
        try:
            data = json.loads(row.get("value", "{}"))
            meta = data.get("metadata", {})
            depends_on = meta.get("depends_on", [])

            if completed_task_id not in depends_on:
                continue

            if data.get("status") != TaskStatus.open.value:
                continue

            # Check if ALL dependencies are completed
            all_done = True
            for dep_id in depends_on:
                dep_result = await _stdb_sql(
                    f"SELECT value FROM memory_entry WHERE key = 'task:{dep_id}'"
                )
                dep_rows = dep_result if isinstance(dep_result, list) else dep_result.get("rows", [])
                if dep_rows:
                    dep_data = json.loads(dep_rows[0].get("value", "{}"))
                    if dep_data.get("status") != TaskStatus.completed.value:
                        all_done = False
                        break
                else:
                    all_done = False
                    break

            if all_done:
                # Auto-assign to the designated agent if one exists
                assigned = meta.get("assigned_agent")
                if assigned:
                    data["status"] = TaskStatus.claimed.value
                    meta["auto_claimed_at"] = int(time.time())
                    data["metadata"] = meta
                    task_key = row.get("key", "")
                    await _stdb_call("store_memory", [
                        "task", task_key, json.dumps(data),
                        "hermes-optx-api", assigned, 5, "",
                    ])

        except (json.JSONDecodeError, AttributeError):
            continue


# ---------------------------------------------------------------------------
# Swarm endpoints
# ---------------------------------------------------------------------------

@router.post("/tasks/swarm")
async def create_swarm(request: Request):
    """Create a swarm — decompose a goal into a DAG of subtasks.

    Body: {"goal": "...", "agent_count": 3, "strategy": "parallel|sequential|dag"}
    """
    body = await request.json()
    goal = body.get("goal", "")
    strategy = body.get("strategy", "parallel")
    agent_count = body.get("agent_count", 1)

    if not goal:
        raise HTTPException(status_code=400, detail="Goal is required")

    # Use HEDGEHOG/Grok to decompose the goal into subtasks
    subtasks = await _decompose_goal(goal, strategy, agent_count)

    # Create all subtasks in SpacetimeDB
    created = []
    task_ids = []
    for i, st in enumerate(subtasks):
        depends = []
        if strategy == "sequential" and task_ids:
            depends = [task_ids[-1]]
        elif strategy == "dag":
            depends = st.get("depends_on", [])

        task = TaskCreate(
            title=st["title"],
            description=st["description"],
            priority=st.get("priority", 5),
            capabilities=st.get("capabilities", []),
            depends_on=depends,
            assigned_agent=st.get("agent", "astrojoe"),
        )
        result = await create_task(task)
        task_ids.append(result["task_id"])
        created.append(result)

    return {
        "swarm_id": str(uuid.uuid4())[:8],
        "goal": goal,
        "strategy": strategy,
        "tasks": created,
        "total": len(created),
    }


async def _decompose_goal(goal: str, strategy: str, agent_count: int) -> list[dict]:
    """Use HEDGEHOG to decompose a goal into subtasks."""
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                "http://100.85.183.16:8811/v1/chat/completions",
                json={
                    "model": "grok-4.20-0309-reasoning",
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "You are a task decomposer. Given a goal, break it into "
                                f"subtasks for {agent_count} agent(s) using {strategy} strategy. "
                                "Return ONLY a JSON array of objects with keys: "
                                "title, description, priority (1-10), capabilities (list), agent (string). "
                                "No markdown, no explanation, just the JSON array."
                            ),
                        },
                        {"role": "user", "content": goal},
                    ],
                    "max_tokens": 2048,
                    "temperature": 0.3,
                },
                headers={
                    "Authorization": "Bearer sk-hedgehog-local",
                    "Content-Type": "application/json",
                },
            )
            if resp.status_code == 200:
                data = resp.json()
                content = data["choices"][0]["message"]["content"]
                # Strip markdown fences if present
                content = content.strip()
                if content.startswith("```"):
                    content = content.split("\n", 1)[1]
                if content.endswith("```"):
                    content = content.rsplit("```", 1)[0]
                return json.loads(content.strip())
    except Exception:
        pass

    # Fallback: single task
    return [{"title": goal, "description": goal, "priority": 5, "capabilities": [], "agent": "astrojoe"}]


async def _verify_gaze_attestation(agent_name: str) -> bool:
    """Check AARON Router for a recent gaze attestation for this agent."""
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(
                "http://100.85.183.16:8888/gaze/recent",
                params={"agent": agent_name},
                headers={"Content-Type": "application/json"},
            )
            if resp.status_code == 200:
                data = resp.json()
                if data.get("verified") and data.get("age_seconds", 999) < 300:
                    return True
    except Exception:
        pass
    return False


@router.get("/tasks/stats")
async def task_stats():
    """Get task statistics."""
    result = await _stdb_sql(
        "SELECT value FROM memory_entry WHERE category = 'task'"
    )
    rows = result if isinstance(result, list) else result.get("rows", [])

    counts = {s.value: 0 for s in TaskStatus}
    total = 0
    for row in rows:
        try:
            data = json.loads(row.get("value", "{}"))
            status = data.get("status", "Open")
            counts[status] = counts.get(status, 0) + 1
            total += 1
        except (json.JSONDecodeError, AttributeError):
            continue

    return {"total": total, "by_status": counts}
