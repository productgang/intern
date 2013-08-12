from twisted.internet import reactor, protocol
from autobahn.websocket import (
    WebSocketServerFactory,
    WebSocketServerProtocol,
    listenWS)
from twisted.python.log import startLogging, msg
import sys
import os
startLogging(sys.stdout)
import json
import threading
from flask import Flask, send_from_directory, render_template
app = Flask(__name__)


@app.route('/')
def intern():
    for service in config['services']:
        config['services'][service]['status'] = 'stopped'
    for process in factory.processes:
        if process['id'] in config['services']:
            config['services'][process['id']]['status'] = 'running'
    return render_template('intern.html', config=config)


@app.route('/static/<path:filename>')
def serve_static(filename):
    return send_from_directory('static', filename)


@app.route('/process/stop/<identifier>/')
def stop_process(identifier):
    if identifier not in config['services']:
        return 'Service not found!'
    processes_running = [p['id'] for p in factory.processes]
    if identifier not in processes_running:
        return 'Service not running!'
    service = filter(lambda s: s['id'] == identifier, factory.processes)[0]
    service['process'].stop()
    return 'Stopped %s' % identifier


class ProcessProtocol(protocol.ProcessProtocol):
    def __init__(self, websocket_factory, identifier):
        self.ws = websocket_factory
        self.buffer = []
        self.identifier = identifier

    def outReceived(self, message):
        self.ws.broadcast(json.dumps({
            'process': self.identifier,
            'line': message.encode('utf-8')
        }))
        self.buffer.append(message)
        self.buffer = self.buffer[-10:]

    def errReceived(self, data):
        print 'Error: %s' % data

    def processExited(self, status):
        self.ws.broadcast(json.dumps({
            'process': self.identifier,
            'action': 'stopped'
        }))
        self.ws.processes = filter(
            lambda p: p['id'] != self.identifier, self.ws.processes)

    def connectionMade(self):
        self.ws.broadcast(json.dumps({
            'process': self.identifier,
            'action': 'started'
        }))

    def stop(self):
        self.transport.signalProcess('KILL')
        self.ws.processes = filter(
            lambda p: p['id'] != self.identifier, self.ws.processes)


class WebSocketProcess(WebSocketServerProtocol):
    def onOpen(self):
        self.factory.register(self)
        for process in self.factory.processes:
            for line in process['process'].buffer:
                msg('Sending buffer')
                self.sendMessage(json.dumps(
                    {
                        'process': process['id'],
                        'line': line
                    }))

    def onMessage(self, msg, binary):
        data = json.loads(msg)
        if 'start' in data:
            identifier = data['start']
            if identifier not in config['services']:
                return 'Service not found!'
            processes_running = [p['id'] for p in factory.processes]
            if identifier in processes_running:
                return 'Service already running!'
            service = config['services'][identifier]
            factory.runProcess(identifier, service['command'], service['args'])

    def connectionLost(self, reason):
        WebSocketServerProtocol.connectionLost(self, reason)
        self.factory.unregister(self)


class WebSocketProcessFactory(WebSocketServerFactory):
    protocol = WebSocketProcess

    def __init__(self, *args, **kwargs):
        WebSocketServerFactory.__init__(self, *args, **kwargs)
        self.clients = []
        self.processes = []

    def register(self, client):
        msg('Registered client %s' % client)
        if not client in self.clients:
            self.clients.append(client)

    def unregister(self, client):
        msg('Unregistered client %s' % client)
        if client in self.clients:
            self.clients.remove(client)

    def broadcast(self, message):
        msg('Broadcasting')
        for client in self.clients:
            client.sendMessage(message)

    def runProcess(self, id_, command, args):
        process = ProcessProtocol(self, id_)
        self.processes.append({'id': id_, 'process': process})
        reactor.spawnProcess(
            process, command, [command] + args, {'HOME': os.getenv('HOME')},
            usePTY=True, path=config['services'][id_]['path'])


class FlaskThread(threading.Thread):
    def __init__(self, app):
        threading.Thread.__init__(self)
        self.app = app

    def run(self):
        self.app.run('0.0.0.0')


if __name__ == '__main__':
    with open('config.json', 'r') as configfile:
        config = json.load(configfile)

    flaskthread = FlaskThread(app)
    flaskthread.start()

    # Running in main thread, since signals only work here
    factory = WebSocketProcessFactory(
        'ws://0.0.0.0:9000', debug=False)
    listenWS(factory)
    reactor.run()

    flaskthread._Thread__stop()
