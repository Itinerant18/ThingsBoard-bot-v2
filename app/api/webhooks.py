import json
import logging

from fastapi import APIRouter, HTTPException, Request

from app.auth.security import verify_webhook
from app.ingest.parse import EventParse
from app.ingest.write import write_event

router = APIRouter(tags=["webhooks"])
logger = logging.getLogger(__name__)


@router.post("/webhooks/thingsboard", status_code=202)
async def thingsboard_webhook(request: Request) -> dict[str, bool]:
    """HMAC-verify and ENQUEUE (diagram: webhook -> RabbitMQ -> consumer). The consumer
    owns parsing, persistence, and fleet-state folding. If the broker is unreachable
    and the fallback is enabled, degrade to the old synchronous DB write so ingestion
    survives a broker outage (at the cost of no per-event Redis fold for that event).
    """
    body = await verify_webhook(request, request.app.state.settings)
    # Parse eagerly even on the queue path: reject garbage at the edge with a 4xx
    # instead of poisoning the queue and dead-lettering it in the consumer.
    try:
        event = EventParse.from_payload(
            json.loads(body), request.app.state.settings.webhook_default_tenant_id
        )
    except (json.JSONDecodeError, KeyError, ValueError) as exc:
        logger.warning("rejected webhook payload: %s", exc)
        raise HTTPException(status_code=422, detail="Malformed event payload") from exc

    publisher = getattr(request.app.state, "publisher", None)
    if publisher is not None:
        try:
            await publisher.publish(
                body if isinstance(body, bytes) else body.encode(), customer=event.customer_id
            )
        except Exception:
            logger.exception("publish to queue failed")
            if not request.app.state.settings.webhook_direct_write_fallback:
                raise HTTPException(status_code=503, detail="Event queue unavailable") from None
        else:
            return {"queued": True}

    async with request.app.state.session_factory() as session:
        inserted = await write_event(session, event)
    return {"queued": False, "accepted": inserted}
