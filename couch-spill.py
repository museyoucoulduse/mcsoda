#!/usr/bin/env python

import re
import sys
import copy
import math
import time
import socket
import threading
import mcsoda

class Reader(threading.Thread):
    def __init__(self, src, reader_go, reader_done):
        self.src = src
        self.reader_go = reader_go
        self.reader_done = reader_done
        self.inflight = 0
        threading.Thread.__init__(self)

    def run(self):
        self.reader_go.wait()
        self.reader_go.clear()
        while True:
            data = self.src.recv(4096)
            if not data:
                break

            found = len(re.findall("HTTP/1.1 ", data))

            self.inflight -= found
            if self.inflight == 0:
                self.reader_done.set()
                self.reader_go.wait()
                self.reader_go.clear()


# Stream some mcsoda onto a couch for performance testing.
#
class StoreCouch(mcsoda.Store):

    def connect(self, target, user, pswd, cfg, cur):
        self.cfg = cfg
        self.cur = cur
        self.target = target
        self.host_port = (target + ":5984").split(':')[0:2]
        self.host_port[1] = int(self.host_port[1])
        self.queue = []
        self.ops = 0
        self.seq = 1
        self.skt = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.skt.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.skt.connect(tuple(self.host_port))
        self.reader_go = threading.Event()
        self.reader_done = threading.Event()
        self.skt_reader = Reader(self.skt, self.reader_go, self.reader_done)
        self.skt_reader.daemon = True
        self.skt_reader.start()

    def gen_doc(self, key_num, key_str, min_value_size, json=True, cache=None):
        # Always json and never cache.
        #
        suffix_ex = '"_rev":"%s-0286dbb6323b61e7f0be3ba5d1633985",' % (self.seq,)

        # suffix_ex += '"_revisions":{"start":1,"ids":["0286dbb6323b61e7f0be3ba5d1633985"]},'

        self.seq = self.seq + 1

        return mcsoda.gen_doc_string(key_num, key_str, min_value_size,
                                     self.cfg['suffix'][min_value_size],
                                     True, cache=None, key_name="_id",
                                     suffix_ex=suffix_ex,
                                     whitespace=False)

    def command(self, c):
        self.queue.append(c)
        if len(self.queue) > (self.cur.get('batch') or \
                              self.cfg.get('batch', 100)):
            self.flush()
            return True
        return False

    def flush(self):
        a = [ "POST /default/_bulk_docs HTTP/1.1\r\n" \
              "X-Couch-Full-Commit: false\r\n" \
              "Content-Type: application/json\r\n" \
              "Accept: application/json\r\n" \
              "Host: %s:%s\r\n" % (self.host_port[0], self.host_port[1]),
              "Content-Length: ", None, "\r\n\r\n",
              '{"new_edits":false,"docs":[' ]
        x = len(a[-1]) # Content length.
        n = 0          # Number of actual docs to be sent.
        for c in self.queue:
            cmd, key_num, key_str, doc, expiration = c
            if doc:
                if n > 0:
                    a.append(',')
                    x += 1
                a.append(doc)
                x += len(doc)
                n += 1
        a.append("]}")
        x += 2

        if n > 0:
            a[2] = str(x) # Fill the content length placeholder.
            m = ''.join(a)
            self.skt_reader.inflight += 1
            self.skt.send(m)
            self.reader_go.set()
            self.reader_done.wait()
            self.reader_done.clear()

        self.ops += len(self.queue)
        self.queue = []


if __name__ == "__main__":
    if sys.argv[1].find("http") != 0:
        raise Exception("usage: %s http://HOST:5984 ..." % (sys.argv[0],))

    argv = (' '.join(sys.argv) + ' doc-gen=0').split(' ')

    mcsoda.main(argv, protocol="http", stores=[StoreCouch()])