# pylint: disable=missing-module-docstring,missing-function-docstring,missing-class-docstring,protected-access,unused-argument,too-few-public-methods,no-member,use-implicit-booleaness-not-comparison,line-too-long,invalid-name,too-many-statements,too-many-instance-attributes,wrong-import-position,wrong-import-order,deprecated-module,too-many-locals,too-many-lines,attribute-defined-outside-init,unexpected-keyword-arg,duplicate-code
import proxy as proxy_module


class DummyMQTTMixin:
    def _state_topic(self, device_id, table):
        if table:
            return f"{proxy_module.MQTT_NAMESPACE}/{device_id}/{table}/state"
        return f"{proxy_module.MQTT_NAMESPACE}/{device_id}/state"

    def _map_data_for_publish(self, data, *, table, target_device_id):
        payload = {k: v for k, v in data.items() if not k.startswith("_")}
        return payload, len(payload)

    def state_topic(self, device_id, table):
        return self._state_topic(device_id, table)

    def map_data_for_publish(self, data, *, table, target_device_id):
        return self._map_data_for_publish(
            data, table=table, target_device_id=target_device_id)

    def get_cached_payload(self, topic):
        return self._last_payload_by_topic.get(topic)

    def set_cached_payload(self, topic, payload):
        self._last_payload_by_topic[topic] = payload
