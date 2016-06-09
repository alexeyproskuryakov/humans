import logging
import random
import time
import traceback
from collections import defaultdict

import requests
from praw import Reddit
from praw.objects import MoreComments, Submission

from wsgi import properties
from wsgi.db import HumanStorage, CommentsStorage
from wsgi.properties import WEEK
from wsgi.rr_people import RedditHandler, USER_AGENTS, A_CONSUME, A_VOTE, A_COMMENT, A_POST, A_SUBSCRIBE, normalize, \
    A_FRIEND, re_url
from wsgi.rr_people.posting.posts import PS_POSTED, PS_ERROR
from wsgi.rr_people.posting.posts_managing import PostHandler

log = logging.getLogger("consumer")


def _get_random_near(slice, index, max):
    slice_indices = map(lambda x: x[0], enumerate(slice))
    r_count = random.randint(max / 2, max)
    l_count = random.randint(max / 2, max)

    temp_r = set()
    for _ in slice_indices[index:]:
        r_id = random.randint(index + 1, slice_indices[-1])
        temp_r.add(r_id)
        if len(temp_r) >= r_count:
            break
    res_r = [slice[i] for i in temp_r]

    temp_l = set()
    for _ in slice_indices[:index]:
        l_id = random.randint(0, index - 1)
        temp_l.add(l_id)
        if len(temp_l) >= l_count:
            break
    res_l = [slice[i] for i in temp_l]
    return res_l, res_r


class HumanConfiguration(object):
    def __init__(self, data=None):
        """
        Configuration of rr_people live
        :return:
        """
        if not data:
            self.subscribe = 95
            self.author_friend = 95
            self.comments = 75
            self.comment_vote = 85
            self.comment_friend = 95
            self.comment_url = 85
            self.post_vote = 65

            self.comment_mwt = 5
            self.max_wait_time = 30

            self.max_posts_near_commented = 50

        elif isinstance(data, dict):
            for k, v in data.iteritems():
                self.__dict__[k] = int(v)

    def set(self, conf_name, conf_val):
        if conf_name in self.__dict__:
            self.__dict__[conf_name] = conf_val

    @property
    def data(self):
        return self.__dict__


class Consumer(RedditHandler):
    def __init__(self, login):
        super(Consumer, self).__init__()
        self.db = HumanStorage(name="consumer %s" % login)
        self.comment_storage = CommentsStorage(name="consumer %s" % login)
        self.posts_handler = PostHandler("consumer %s" % login)

        human_configuration = self.db.get_human_config(login)
        login_credentials = self.db.get_human_access_credentials(login)
        if not login_credentials:
            raise Exception("Can not have login credentials at %s", login)

        self.subscribed_subreddits = set(human_configuration.get("ss", [])) or set()
        self.friends = set(human_configuration.get("frds", [])) or set()
        self.last_friend_add = human_configuration.get("last_friend_add") or time.time() - WEEK

        self.init_engine(login_credentials)
        self.init_work_cycle()

        live_config = human_configuration.get("live_config")
        if not live_config:
            self.configuration = HumanConfiguration()
            self.db.set_human_live_configuration(login, self.configuration)
        else:
            self.configuration = HumanConfiguration(live_config)

        self._used = set()
        self._last_loads = {}
        self._sub_posts = {}
        self._last_post_ids = defaultdict(int)

        log.info("Write human [%s] inited with credentials \n%s"
                 "\nConfiguration: \n%s"
                 "\nFriends: %s"
                 "\nSubscribed breddits:%s" % (login,
                                               "\n".join(["%s:\t%s" % (k, v) for k, v in
                                                          login_credentials.get("info", {}).iteritems()]),
                                               "\n".join(["%s:\t%s" % (k, v) for k, v in
                                                          self.configuration.data.iteritems()]),
                                               self.friends,
                                               self.subscribed_subreddits
                                               ))

    def init_engine(self, login_credentials):
        self.user_agent = login_credentials.get("user_agent", random.choice(USER_AGENTS))
        self.name = login_credentials["user"]

        r = Reddit(self.user_agent)

        r.set_oauth_app_info(login_credentials['client_id'], login_credentials['client_secret'],
                             login_credentials['redirect_uri'])
        r.set_access_credentials(**login_credentials.get("info"))
        r.login(login_credentials["user"], login_credentials["pwd"], disable_warning=True)

        self.access_information = login_credentials.get("info")
        self.login_credentials = {"user": self.name, "pwd": login_credentials["pwd"]}
        self.reddit = r
        self.refresh_token()

    def refresh_token(self):
        self.access_information = self.reddit.refresh_access_information(self.access_information['refresh_token'])
        self.db.update_human_access_credentials_info(self.name, self.access_information)
        self.reddit.login(self.login_credentials["user"], self.login_credentials["pwd"], disable_warning=True)

    def incr_counter(self, name):
        self.counters[name] += 1

    def decr_counter(self, name):
        self.counters[name] -= 1

    @property
    def action_function_params(self):
        return self.__action_function_params

    @action_function_params.setter
    def action_function_params(self, val):
        self.__action_function_params = val
        self.counters = {A_CONSUME: 0, A_VOTE: 0, A_COMMENT: 0, A_POST: 0}

    def init_work_cycle(self):
        consuming = random.randint(properties.min_consuming, properties.max_consuming)
        production = 100 - consuming

        prod_voting = random.randint(properties.min_voting, properties.max_voting)
        prod_commenting = 100 - prod_voting

        prod_posting = prod_commenting / random.randint(2, 4)
        prod_commenting -= prod_posting

        voting = (prod_voting * production) / 100
        commenting = (prod_commenting * production) / 100
        posting = (prod_posting * production) / 100

        self.action_function_params = {A_CONSUME: consuming,
                                       A_VOTE: voting,
                                       A_COMMENT: commenting,
                                       A_POST: posting
                                       }
        log.info("MY [%s] WORK CYCLE: %s" % (self.name, self.action_function_params))
        return self.action_function_params

    def can_do(self, action):
        """
        Action
        :param action: can be: [vote, comment, consume]
        :return:  true or false
        """
        summ = sum(self.counters.values())
        action_count = self.counters[action]
        granted_perc = self.action_function_params.get(action)
        current_perc = int((float(action_count) / (summ if summ else 100)) * 100)

        return current_perc <= granted_perc

    def must_do(self, action):
        result = True
        for another_action in self.action_function_params.keys():
            if another_action == action:
                continue
            result = result and not self.can_do(another_action)
        return result

    def _is_want_to(self, coefficient):
        return coefficient >= 0 and random.randint(0, properties.want_coefficient_max) >= coefficient

    def register_step(self, step_type, info=None):
        if step_type in self.counters:
            self.incr_counter(step_type)
        if step_type == A_FRIEND:
            self.last_friend_add = time.time()

        self.db.save_log_human_row(self.name, step_type, info or {})
        self.persist_state()
        log.info("step by [%s] |%s|: %s", self.name, step_type, info)

        if info and info.get("fullname"):
            self._used.add(info.get("fullname"))

    @property
    def state(self):
        return {"ss": list(self.subscribed_subreddits),
                "frds": list(self.friends),
                "last_friend_add": self.last_friend_add
                }

    def can_friendship_create(self, friend_name):
        return friend_name not in self.friends and time.time() - self.last_friend_add > random.randint(WEEK / 5, WEEK)

    def persist_state(self):
        self.db.update_human_internal_state(self.name, state=self.state)

    def do_see_post(self, post):
        """
        1) go to his url with yours useragent, wait random
        2) random check comments and random check more comments
        3) random go to link in comments
        #todo friend five in week
        :param post:
        :return:
        """
        try:
            res = requests.get(post.url, headers={"User-Agent": self.user_agent})
            self.register_step(A_CONSUME,
                               info={"url": post.url, "permalink": post.permalink, "fullname": post.fullname})
        except Exception as e:
            log.warning("Can not see post %s url %s \n EXCEPT [%s] \n %s" % (
                post.fullname, post.url, e, traceback.format_exc()))

        wt = self.wait(self.configuration.max_wait_time)

        if self._is_want_to(self.configuration.post_vote) and self.can_do("vote"):
            post_vote_count = random.choice([1, -1])
            try:
                post.vote(post_vote_count)
                self.register_step(A_VOTE, info={"fullname": post.fullname, "vote": post_vote_count})
            except Exception as e:
                log.exception(e)

            self.wait(self.configuration.max_wait_time / 2)

        if self._is_want_to(self.configuration.comments) and wt > self.configuration.comment_mwt:  # go to post comments
            for comment in post.comments:
                if self._is_want_to(self.configuration.comment_vote) and self.can_do("vote"):  # voting comment
                    vote_count = random.choice([1, -1])
                    try:
                        comment.vote(vote_count)
                        self.register_step(A_VOTE, info={"fullname": comment.fullname, "vote": vote_count})
                        self.wait(self.configuration.max_wait_time / 10)
                    except Exception as e:
                        log.exception(e)

                    if self._is_want_to(self.configuration.comment_friend) and \
                                    vote_count > 0 and \
                            self.can_friendship_create(comment.author.name):  # friend comment author
                        try:
                            c_author = comment.author
                            c_author.friend()
                            self.friends.add(c_author.name)
                            self.register_step(A_FRIEND, info={"friend": c_author.name, "from": "comment"})
                            log.info("%s was add friend from comment %s because want coefficient is: %s",
                                     (self.name, comment.fullname, self.configuration.comment_friend))
                            self.wait(self.configuration.max_wait_time / 10)
                        except Exception as e:
                            log.exception(e)

                if self._is_want_to(self.configuration.comment_url):  # go to url in comment
                    if isinstance(comment, MoreComments):
                        continue

                    urls = re_url.findall(comment.body)
                    for url in urls:
                        try:
                            res = requests.get(url, headers={"User-Agent": self.user_agent})
                            log.info("%s was consume comment url: %s" % (self.name, res.url))
                        except Exception as e:
                            pass
                    if urls:
                        self.register_step(A_CONSUME, info={"urls": urls})

            self.wait(self.configuration.max_wait_time / 5)

        if self._is_want_to(self.configuration.subscribe) and \
                        post.subreddit.display_name not in self.subscribed_subreddits:  # subscribe sbrdt
            try:
                self.reddit.subscribe(post.subreddit.display_name)
                self.subscribed_subreddits.add(post.subreddit.display_name)
                self.register_step(A_SUBSCRIBE, info={"sub": post.subreddit.display_name})
                self.wait(self.configuration.max_wait_time / 5)
            except Exception as e:
                log.exception(e)

        if self._is_want_to(self.configuration.author_friend) and \
                self.can_friendship_create(post.author.name):  # friend post author
            try:
                post.author.friend()
                self.friends.add(post.author.name)
                self.register_step(A_FRIEND, info={"fullname": post.author.name, "from": "post"})
                log.info("%s was add friend from post %s because want coefficient is: %s" % (
                    self.name, post.fullname, self.configuration.author_friend))
                self.wait(self.configuration.max_wait_time / 5)
            except Exception as e:
                log.exception(e)

    def set_configuration(self, configuration):
        self.configuration = configuration
        log.info("For %s configuration is setted: %s" % (self.name, configuration.data))

    def wait(self, max_wait_time):
        if max_wait_time > 1:
            wt = random.randint(1, max_wait_time)
            time.sleep(wt)
            return wt
        return max_wait_time

    def do_comment_post(self, post_fullname, subreddit_name, comment_id):
        near_posts = self.get_hot_and_new(subreddit_name)
        for i, _post in enumerate(near_posts):
            if _post.fullname == post_fullname:
                see_left, see_right = _get_random_near(near_posts, i, self.configuration.max_posts_near_commented)
                try:
                    for p_ind in see_left:
                        self.do_see_post(p_ind)
                except Exception as e:
                    log.error(e)

                try:
                    for comment in filter(lambda comment: isinstance(comment, MoreComments), _post.comments):
                        comment.comments()
                        if random.randint(0, 10) > 6:
                            break
                except Exception as e:
                    log.error(e)

                try:
                    comment_text = self.comment_storage.get_text(comment_id)
                    text_hash = hash(normalize(comment_text))
                    if self.comment_storage.can_comment_post(self.name,
                                                             post_fullname=_post.fullname,
                                                             hash=text_hash):
                        response = _post.add_comment(comment_text)
                        self.comment_storage.set_post_commented(_post.fullname,
                                                                by=self.name,
                                                                hash=text_hash)
                        self.register_step(A_COMMENT, info={"fullname": post_fullname, "sub": subreddit_name,
                                                            "response": response.__dict__})
                except Exception as e:
                    log.error(e)

                try:
                    for p_ind in see_right:
                        self.do_see_post(p_ind)
                except Exception as e:
                    log.error(e)

    def do_live_random(self, max_actions=100, posts_limit=500):

        def get_hot_or_new(sbrdt):
            funcs = [lambda: sbrdt.get_hot(limit=posts_limit), lambda: sbrdt.get_new(limit=posts_limit)]
            f = random.choice(funcs)
            return list(f())

        counter = 0
        subs = self.db.get_human_subs(self.name)
        if not subs:
            log.error("For %s not any subs at config :(", self.name)
            return

        random_sub = random.choice(subs)
        if random_sub not in self._sub_posts or \
                                time.time() - self._last_loads.get(random_sub,
                                                                   time.time()) > properties.TIME_TO_RELOAD_SUB_POSTS:
            sbrdt = self.get_subreddit(random_sub)
            posts = get_hot_or_new(sbrdt)
            self._sub_posts[random_sub] = posts
            self._last_loads[random_sub] = time.time()
        else:
            posts = self._sub_posts[random_sub]

        w_k = random.randint(properties.want_coefficient_max / 2, properties.want_coefficient_max)

        start_from = self._last_post_ids.get(random_sub, 0)
        for i, post in enumerate(posts, start=start_from):
            if post.fullname not in self._used and self._is_want_to(w_k):
                self.do_see_post(post)
                counter += 1
            if random.randint(int(max_actions / 1.5), max_actions) < counter:
                self._last_post_ids[random_sub] = i
                return

    def do_post(self):
        post = self.posts_handler.get_post(self.name)
        if not post:
            log.warn("no posts for me [%s] :(" % self.name)
            return

        subreddit = self.get_subreddit(post.for_sub)

        time_to_write = int(len(post.title) / random.randint(2, 4))
        log.info("will posting and write post on %s seconds" % time_to_write)
        time.sleep(time_to_write)

        result = subreddit.submit(save=True, title=post.title, url=post.url)

        log.info("was post at [%s]; title: [%s]; url: [%s]" % (post.for_sub, post.title, post.url))
        if isinstance(result, Submission):
            self.register_step(A_POST,
                               {"fullname": result.fullname, "sub": post.for_sub, 'title': post.title, 'url': post.url})
            self.posts_handler.set_post_state(post.url_hash, PS_POSTED)
            log.info("OK! result: %s" % (result))
        else:
            self.posts_handler.set_post_state(post.url_hash, PS_ERROR)
            log.info("NOT OK :( result: %s" % (result))


class FakeConsumer(Consumer):
    def __init__(self, login):
        super(FakeConsumer, self).__init__(login)

    def refresh_token(self):
        log.info("REFRESH TOKEN")

    def do_post(self):
        log.info("DO POSTING...")

    def do_comment_post(self, post_fullname, subreddit_name, comment_id):
        text = self.comment_storage.get_text(comment_id)
        log.info("DO COMMENT POST AT: %s POST: %s COMMENT_ID: %s\nCOMMENT_TEXT: %s" % (
            subreddit_name, post_fullname, comment_id, text))

    def do_see_post(self, post):
        log.info("DO SEE POST %s" % post)

    def do_live_random(self, max_actions=100, posts_limit=500):
        log.info("DO LIVE RANDOM %s %s" % (max_actions, posts_limit))
