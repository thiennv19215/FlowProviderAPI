def test_direct_extension_protocol_registers_connection(client):
    with client.websocket_connect("/api/extensions/ws") as ws:
        ws.send_json({
            "type":"extension_ready",
            "protocolVersion":7,
            "installationId":"install-test-123456",
            "runtimeId":"chrome",
            "profileId":"profile-test",
            "profileName":"Test Chrome",
        })
        health=client.get("/v1/health").json()
        assert health["extension_connected"] is True
    assert client.get("/v1/health").json()["extension_connected"] is False


def test_previous_extension_subprotocol_remains_compatible(client):
    with client.websocket_connect(
        "/api/extensions/ws",
        subprotocols=["flow-provider-v7","flow-token.legacy-ignored"],
    ) as ws:
        assert ws.accepted_subprotocol=="flow-provider-v7"
        ws.send_json({
            "type":"extension_ready",
            "protocolVersion":7,
            "installationId":"install-legacy-protocol",
        })
        assert client.get("/v1/health").json()["extension_connected"] is True
