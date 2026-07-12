"""
scheduler.py - 매주 금요일 20:00 KST 자동 실행 스케줄러 (APScheduler)
"""

import logging

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger(__name__)


def create_scheduler(pipeline_func, schedule_config: dict) -> BlockingScheduler:
    """
    pipeline_func: 실행할 파이프라인 함수
    schedule_config: config.yaml의 schedule 섹션
    """
    scheduler = BlockingScheduler(timezone=schedule_config.get("timezone", "Asia/Seoul"))

    trigger = CronTrigger(
        day_of_week=schedule_config.get("day_of_week", "fri"),
        hour=int(schedule_config.get("hour", 20)),
        minute=int(schedule_config.get("minute", 0)),
        timezone=schedule_config.get("timezone", "Asia/Seoul"),
    )

    scheduler.add_job(
        pipeline_func,
        trigger=trigger,
        id="weekly_stock_pipeline",
        name="주간 주가 자금동향 파이프라인",
        misfire_grace_time=3600,  # 1시간 이내 미실행 시 즉시 실행
        coalesce=True,            # 중복 실행 방지
    )

    logger.info(
        f"스케줄러 등록: "
        f"매주 {schedule_config.get('day_of_week', 'fri')} "
        f"{schedule_config.get('hour', 20):02d}:"
        f"{schedule_config.get('minute', 0):02d} "
        f"({schedule_config.get('timezone', 'Asia/Seoul')})"
    )
    return scheduler
