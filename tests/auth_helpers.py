import os


def sign_in(client):
    response = client.post('/auth/login', json={'username':os.environ['ADMIN_USERNAME'],'password':os.environ['ADMIN_PASSWORD']},
                           headers={'x-requested-with':'fynd-console'})
    assert response.status_code == 200, response.text
    client.headers['x-csrf-token'] = response.json()['csrf_token']
    return response
