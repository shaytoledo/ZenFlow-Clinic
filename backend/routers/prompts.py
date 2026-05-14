from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from ..database import get_db
from ..models import Project, PromptHistory
from ..schemas import OptimizeRequest, OptimizeResponse, PromptHistoryOut
from ..services.prompt_optimizer import optimize_prompt

router = APIRouter(prefix="/projects/{project_id}/prompts", tags=["prompts"])


@router.post("/optimize", response_model=OptimizeResponse)
async def optimize(project_id: int, body: OptimizeRequest, db: Session = Depends(get_db)):
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    optimized = await optimize_prompt(
        user_input=body.user_input,
        project_context=project.context,
        target_model=body.target_model,
    )

    history = PromptHistory(
        project_id=project_id,
        user_input=body.user_input,
        optimized_prompt=optimized,
        target_model=body.target_model,
    )
    db.add(history)
    db.commit()
    db.refresh(history)

    return OptimizeResponse(
        optimized_prompt=optimized,
        target_model=body.target_model,
        history_id=history.id,
    )


@router.get("/", response_model=list[PromptHistoryOut])
def list_history(project_id: int, db: Session = Depends(get_db)):
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project.prompts


@router.delete("/{history_id}", status_code=204)
def delete_history(project_id: int, history_id: int, db: Session = Depends(get_db)):
    history = db.get(PromptHistory, history_id)
    if not history or history.project_id != project_id:
        raise HTTPException(status_code=404, detail="Not found")
    db.delete(history)
    db.commit()
