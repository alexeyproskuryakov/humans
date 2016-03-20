import logging
import random
from lxml import html

import re
from praw.objects import MoreComments
from requests import get

from wsgi.rr_people import RedditHandler, cmp_by_created_utc, USER_AGENTS, normalize, tokens_equals
from wsgi.rr_people.posting.generator import Generator
from wsgi.rr_people.posting.posts import PostSource, PostChecker, PS_READY
from wsgi.db import DBHandler

COPY = "copy"

log = logging.getLogger("copy")

MIN_RATING = 2
MAX_RATING = 50


class SubredditsRelationsStore(DBHandler):
    def __init__(self):
        super(SubredditsRelationsStore, self).__init__()
        self.sub_col = self.db.get_collection("sub_relations")
        if not self.sub_col:
            self.sub_col = self.db.create_collection("sub_relations")
            self.sub_col.create_index([("name", 1)], unique=True)

    def add_sub_relations(self, sub_name, related_subs):
        result = self.sub_col.update_one({"name": sub_name}, {"$set": {"related":  related_subs}}, upsert=True)
        return result

    def get_related_subs(self, sub_name):
        found = self.sub_col.find_one({"name": sub_name})
        if found:
            return found.get("related", [])
        return []


imgur_url = re.compile("http:\/\/i\.(imgur.+)\.[a-z]{2,5}")
imgur_cb = lambda x: "http://%s" % x

URLS_PROCESSORS = [
    {"re": imgur_url, "cb": imgur_cb}
]


def prepare_url(url):
    for r in URLS_PROCESSORS:
        found = r["re"].findall(url)
        if found and len(found) == 1:
            cb = r.get("cb")
            piece = found[0]
            if cb: return cb(piece)
            return piece
    return url


class CopyPostGenerator(RedditHandler, Generator):
    def __init__(self):
        super(CopyPostGenerator, self).__init__(name=COPY)
        self.sub_store = SubredditsRelationsStore()
        self.user_agent = random.choice(USER_AGENTS)
        self.checker = PostChecker()

    def found_copy_in_sub(self):
        pass

    def get_title(self, url):
        def check_title(title):
            url_tokens = normalize(url, lambda x:x)
            title_tokens = normalize(title, lambda x:x)
            if len(set(url_tokens).intersection(set(title_tokens))) > 0:
                return False
            return True

        try:
            res = get(url, headers={"User-Agent": self.user_agent})
            if res.status_code == 200:
                title = None
                page = html.fromstring(res.content)
                for meta in page.xpath("//meta"):
                    if meta.get("name") and "title" in meta.get("name"):
                        title = meta.get("content")
                        break

                if not title:
                    for title in page.xpath("//title"):
                        title = title.text

                if title and check_title(title):
                    return title

        except Exception as e:
            log.exception(e)

    def get_title_from_comments(self, post, title):
        comments = self.retrieve_comments(post.comments, post.fullname)
        title_tokens = normalize(title, lambda x: x)

        for comment in comments:
            if not isinstance(comment, MoreComments) and comment.created_utc + 3600 * 7 < post.created_utc:
                comment_tokens = normalize(comment.body, lambda x: x)
                if tokens_equals(title_tokens, comment_tokens):
                    return comment.body

    def generate_data(self, subreddit, key_words):
        related_subs = self.sub_store.get_related_subs(subreddit)
        hot_and_new = self.get_hot_and_new(subreddit, sort=cmp_by_created_utc)
        for post in hot_and_new:
            url_hash = hash(post.url)
            if self.checker.get_post_state(url_hash):
                continue
            if post.ups > MIN_RATING and post.ups < MAX_RATING:
                title = self.get_title(prepare_url(post.url))
                post_title = post.title
                if not title or len(title.strip()) == len(post_title.strip()):
                    comments_title = self.get_title_from_comments(post, post_title)
                    if comments_title:
                        title = comments_title
                    else:
                        continue
                if title:
                    self.checker.set_post_state(url_hash, PS_READY)
                    yield PostSource(post.url, title.strip(), for_sub=random.choice(related_subs))


if __name__ == '__main__':
    cpg = CopyPostGenerator()
    cpg.generate_data("woahdude", [])