from wsgi.rr_people.commenting.connection import CommentsStorage, CS_READY_FOR_COMMENT, CommentRedisQueue
from wsgi.rr_people.human import Human

__doc__="""
think that it must be at test env
"""
def prepare_comments_queue():
    cs = CommentsStorage("test")
    cq = CommentRedisQueue()

    for comment in cs.comments.find({"state":CS_READY_FOR_COMMENT}):
        cq.put_comment(comment.get("sub"), comment.get("_id"))

def test_human_comment():
    h = Human("Shlak2k15")
    h.do_comment_post(sub="funny")

if __name__ == '__main__':
    # prepare_comments_queue()
    test_human_comment()