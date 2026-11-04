from fastapi.testclient import TestClient
import main


def test_frontend_runtime_assets_are_available():
    client=TestClient(main.app)
    try:
        root=client.get('/')
        assert root.status_code==200
        assert '/static/app.js' in root.text and '/static/app.css' in root.text
        script=client.get('/static/app.js')
        styles=client.get('/static/app.css')
        assert script.status_code==200 and 'javascript' in script.headers['content-type']
        assert styles.status_code==200 and 'text/css' in styles.headers['content-type']
    finally:client.close()
