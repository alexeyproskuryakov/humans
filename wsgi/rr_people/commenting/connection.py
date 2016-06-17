# coding=utf-8
import logging
import time

from wsgi.db import DBHandler
from wsgi.properties import comment_redis_address, comment_redis_password, comment_redis_port
from wsgi.properties import comments_mongo_uri, comments_db_name
from wsgi.rr_people.queue import RedisHandler

log = logging.getLogger("comments")


class CommentsStorage(DBHandler):
    #todo end refactoring with reader

    def __init__(self, name="?"):
        super(CommentsStorage, self).__init__(name=name, uri=comments_mongo_uri, db_name=comments_db_name)
        self.comments = self.db.get_collection("comments")
        if not self.comments:
            self.comments = self.db.create_collection(
                "comments",
                capped=True,
                size=1024 * 1024 * 256,
            )
            self.comments.drop_indexes()

            self.comments.create_index([("fullname", 1)], unique=True)
            self.comments.create_index([("commented", 1)], sparse=True)
            self.comments.create_index([("ready_for_comment", 1)], sparse=True)
            self.comments.create_index([("text_hash", 1)], sparse=True)

    def set_post_commented(self, post_fullname, by, hash):
        found = self.comments.find_one({"fullname": post_fullname, "commented": {"$exists": False}})
        if not found:
            to_add = {"fullname": post_fullname, "commented": True, "time": time.time(), "text_hash": hash, "by": by}
            self.comments.insert_one(to_add)
        else:
            to_set = {"commented": True, "text_hash": hash, "by": by, "time": time.time()}

            self.comments.update_one({"fullname": post_fullname}, {"$set": to_set, "$unset": {"comment_body": 1}}, )

    def can_comment_post(self, who, post_fullname, hash):
        q = {"by": who, "commented": True, "$or": [{"fullname": post_fullname}, {"text_hash": hash}]}
        found = self.comments.find_one(q)
        return found is None

    def get_text(self, comment_id):
        self.comments.find({"_id": comment_id})

    def get_posts_ready_for_comment(self):
        return list(self.comments.find({"ready_for_comment": True, "commented": {"$exists": False}}))

    def get_post(self, post_fullname):
        found = self.comments.find_one({"fullname": post_fullname})
        return found

NEED_COMMENT = "need_comment"
QUEUE_CF = lambda x: "cf_queue_%s" % x


class CommentRedisQueue(RedisHandler):
    """
    Recommendations from reader:

    post_ids = comment_queue.get_all_comments_post_ids(sub)
    posts = map(lambda x: {"url": x.get("post_url"), "fullname": x.get("fullname"), "text": x.get("text")},
                comment_storage.get_posts(post_ids))


    """
    def __init__(self, name="?", clear=False, host=None, port=None, pwd=None, db=None):
        super(CommentRedisQueue, self).__init__("comment queue %s" % name, clear,
                                                comment_redis_address,
                                                comment_redis_port,
                                                comment_redis_password,
                                                0)

    def need_comment(self, sbrdt):
        self.redis.publish(NEED_COMMENT, sbrdt)

    def pop_comment(self, sbrdt):
        result = self.redis.lpop(QUEUE_CF(sbrdt))
        log.debug("redis: get by %s\nthis: %s" % (sbrdt, result))
        return result

    def get_all_comments_post_ids(self, sbrdt):
        result = self.redis.lrange(QUEUE_CF(sbrdt), 0, -1)
        return list(result)


