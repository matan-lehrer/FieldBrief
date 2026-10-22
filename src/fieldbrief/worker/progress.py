from faststream.rabbit import ExchangeType, RabbitBroker, RabbitExchange

from fieldbrief.agents.progress import PROGRESS_EXCHANGE, ProgressEvent, Stage, routing_key_for

progress_exchange = RabbitExchange(PROGRESS_EXCHANGE, type=ExchangeType.TOPIC, durable=True)


class RabbitProgressSink:
    def __init__(self, report_id: str, broker: RabbitBroker) -> None:
        self.report_id = report_id
        self._broker = broker

    async def emit(self, stage: Stage, message: str) -> None:
        await self._broker.declare_exchange(progress_exchange)
        event = ProgressEvent(report_id=self.report_id, stage=stage, message=message)
        await self._broker.publish(
            event.model_dump(mode="json"),
            exchange=progress_exchange,
            routing_key=routing_key_for(self.report_id),
        )
