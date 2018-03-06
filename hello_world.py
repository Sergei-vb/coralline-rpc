#!/usr/bin/env python3
import os
import logging
import json
from subprocess import PIPE

import tornado.ioloop
import tornado.web
import tornado.gen
import tornado.options
import tornado.websocket
from tornado.process import Subprocess
import docker

from messaging import tasks


CLIENT = docker.APIClient(base_url='unix://var/run/docker.sock')

SETTINGS = {
    "template_path": os.path.join(os.path.dirname(__file__), "template"),
    "static_path": os.path.join(os.path.dirname(__file__), "static"),
}


class RequestAsync(tornado.web.RequestHandler):
    @tornado.gen.coroutine
    def get(self):
        process = Subprocess(["fortune"], stdout=PIPE, stderr=PIPE, shell=True)
        yield process.wait_for_exit()
        out, err = process.stdout.read(), process.stderr.read()
        self.write(out)


class TasksManager:
    callbacks = dict()

    def register(self, task, callback):
        self.callbacks[task] = callback

    def notifyсallbacks(self):
        for task, callback in self.callbacks.items():
            if task.state == 'PROGRESS':
                callback(task.info['line'])

    # TODO: remove ready items

def make_app():
    return tornado.web.Application([
        (r"/fortune/", RequestAsync),
        (r"/load_from_docker/", DockerWebSocket),
    ], **SETTINGS)


class DockerWebSocket(tornado.websocket.WebSocketHandler):
    def open(self):
        logging.info("WebSocket opened")

    def _url_address(self, **kwargs):
        app.task_manager.register(tasks.build_image.delay(**kwargs), self.callback)
        self.write_message(
            dict(
                output='Building image...',
                method=kwargs["method"]
            )
        )

    def _images(self, **kwargs):
        self.write_message(dict(
            images=[i["RepoTags"][0] for i in CLIENT.images()],
            method=kwargs["method"]))

    def _containers(self, **kwargs):
        self.write_message(dict(
            containers=list(filter(
                lambda x: x[0] != os.getenv("PROJ_CONT"),
                [(i["Names"][0], i["Status"]) for i in CLIENT.containers(
                    all=True)])), method=kwargs["method"]))

    def _create(self, **kwargs):
        CLIENT.create_container(image=kwargs["elem"], command='/bin/sleep 999')
        self._containers(**kwargs)

    def _start(self, **kwargs):
        CLIENT.start(container=kwargs["elem"])
        self._containers(**kwargs)

    def _stop(self, **kwargs):
        CLIENT.stop(container=kwargs["elem"], timeout=1)
        self._containers(**kwargs)

    def _remove(self, **kwargs):
        CLIENT.remove_container(container=kwargs["elem"])
        self._containers(**kwargs)

    def on_message(self, message):
        d = json.loads(message)
        general = dict(url_address=self._url_address,
                       images=self._images,
                       containers=self._containers,
                       create=self._create,
                       start=self._start,
                       stop=self._stop,
                       remove=self._remove,
                       test_celery=self.test_celery,
                       )
        if d["method"] in general.keys():
            general[d["method"]](**d)
        else:
            self.write_message(
                dict(message="Client error", method=d["method"]))

    def on_close(self):
        logging.info("WebSocket closed")

    def test_celery(self):
        tasks.print_hello()

    # def callback(self, line, **kwargs):
    #     self.write_message(
    #         dict(
    #             output=list(json.loads(line).values())[0],
    #             method=kwargs["method"]
    #             )
    #         )
    # def callback(self):
    #     self.write_message(
    #         dict(output='Build completed.'))
    def callback(self, lines):
        # for line in lines:
        line = lines[len(lines)-1]
        self.write_message(
            dict(
                output=list(json.loads(line).values())[0],
                )
            )


if __name__ == "__main__":
    tornado.options.parse_command_line()

    app = make_app()
    app.task_manager = TasksManager()

    periodic_callback = tornado.ioloop.PeriodicCallback(app.task_manager.notifyсallbacks, 3000)
    periodic_callback.start()

    if os.getenv("PORT"):
        logging.info("Use your PORT: {}".format(os.getenv("PORT")))
    else:
        logging.info("Use default PORT: 8889")
    app.listen(os.getenv("PORT", 8889))

    tornado.ioloop.IOLoop.current().start()
