"""Distro routes (spec 4 Distros table + the 6.3 end-of-event no-show pass)."""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlmodel import Session, col, select

from bam.db import get_session
from bam.models import Distro
from bam.schemas import DistroIn, DistroOut, NoShowReport
from bam.services import checkin

router = APIRouter()


class NoShowsIn(BaseModel):
    distro_date: dt.date


@router.post("/distros", response_model=DistroOut)
def create_distro(payload: DistroIn, session: Session = Depends(get_session)) -> Distro:
    """Create a distribution event."""
    distro = Distro(**payload.model_dump())
    session.add(distro)
    session.commit()
    session.refresh(distro)
    return distro


@router.get("/distros", response_model=list[DistroOut])
def list_distros(session: Session = Depends(get_session)) -> list[Distro]:
    """List distribution events, oldest first."""
    return list(
        session.exec(select(Distro).order_by(col(Distro.date_time), col(Distro.id))).all()
    )


@router.post("/distros/no-shows", response_model=NoShowReport)
def process_no_shows(
    payload: NoShowsIn, session: Session = Depends(get_session)
) -> NoShowReport:
    """End-of-distro no-show pass (spec 6.3 no-show sequence, A2/A3)."""
    return checkin.process_no_shows(session, payload.distro_date)
