import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from agent_registry import get_agent, list_agents
from auth import get_optional_user

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("")
async def list_agent_definitions(
  _current_user: Optional[dict] = Depends(get_optional_user),
) -> dict:
  """Return registered agent metadata (from server/web/agents/*.json)."""
  return {"agents": list_agents()}


@router.get("/{agent_id}")
async def get_agent_definition(
  agent_id: str,
  _current_user: Optional[dict] = Depends(get_optional_user),
) -> dict:
  doc = get_agent(agent_id)
  if doc is None:
    raise HTTPException(status_code=404, detail="Unknown agent id")
  return doc
