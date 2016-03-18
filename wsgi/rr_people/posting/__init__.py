import logging
import random
import time
from multiprocessing import Process

from wsgi.db import DBHandler
from wsgi.properties import default_post_generators, DEFAULT_SLEEP_TIME_AFTER_GENERATE_DATA
from wsgi.rr_people import S_WORK, S_SLEEP
from wsgi.rr_people.posting.copy_gen import COPY, CopyPostGenerator, SubredditsRelationsStore
from wsgi.rr_people.posting.imgur_gen import ImgurPostsProvider, IMGUR
from wsgi.rr_people.queue import ProductionQueue

log = logging.getLogger("post_generator")

POST_GENERATOR_OBJECTS = {IMGUR: ImgurPostsProvider, COPY: CopyPostGenerator}


class PostsGeneratorsStorage(DBHandler):
    def __init__(self):
        super(PostsGeneratorsStorage, self).__init__()
        self.generators = self.db.get_collection("generators")
        if not self.generators:
            self.generators = self.db.create_collection('generators')
            self.generators.create_index([("sub",1)], unque=True)

    def set_subreddit_generator(self, sub, generators, key_words):
        self.generators.update_one({"sub": sub}, {"$set": {"gens": generators, "key_words": key_words}}, upsert=True)

    def get_subreddit_generators(self, sub):
        found = self.generators.find_one({"sub": sub})
        if found:
            return dict(found)
        return {"gens": default_post_generators, "key_words": []}


class PostsGenerator(object):
    def __init__(self):
        self.queue = ProductionQueue()
        self.storage = PostsGeneratorsStorage()
        self.gens = {}

    def generate_posts(self, subreddit):
        if subreddit not in self.gens:
            gen_config = self.storage.get_subreddit_generators(subreddit)

            gens = map(lambda x: x().generate_data(subreddit, gen_config.get("key_words")),
                       filter(lambda x: x,
                              map(lambda x: POST_GENERATOR_OBJECTS.get(x),
                                  gen_config.get('gens'))))
            self.gens[subreddit] = gens
        else:
            gens = self.gens[subreddit]

        stopped = set()
        while 1:
            for gen in gens:
                try:
                    post = gen.next()
                    yield post
                except StopIteration:
                    stopped.add(hash(gen))

            if len(stopped) == len(gens):
                break

            random.shuffle(gens)


    def start_generate_posts(self, subrreddit):
        def f():
            self.queue.set_posts_generator_state(subrreddit, S_WORK)
            start = time.time()
            log.info("Will start find posts for [%s] or another" % (subrreddit))
            for post in self.generate_posts(subrreddit):
                self.queue.put_post(subrreddit, post)
            end = time.time()
            sleep_time = random.randint(DEFAULT_SLEEP_TIME_AFTER_GENERATE_DATA / 5,
                                        DEFAULT_SLEEP_TIME_AFTER_GENERATE_DATA)
            log.info(
                    "Was generate posts which found for [%s] at %s seconds... Will trying next after %s" % (
                        subrreddit, end - start, sleep_time))
            self.queue.set_posts_generator_state(subrreddit, S_SLEEP, ex=sleep_time + 1)
            time.sleep(sleep_time)

        ps = Process(name="[%s] posts generator" % subrreddit, target=f)
        ps.start()


if __name__ == '__main__':
    gs = PostsGeneratorsStorage()
    gs.set_subreddit_generator("woahdude", [COPY], [])
    rs = SubredditsRelationsStore()
    rs.add_sub_relations("woahdude", ["cringe", "chopchop", "hui"])
    pg = PostsGenerator()
    for post in pg.generate_posts("woahdude"):
        print post.url
