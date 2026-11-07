from datetime import datetime, timezone

from atproto_client.models.app.bsky.feed.defs import PostView  # type: ignore
from schemas import Link, Media, Post  # your models above


def _parse_iso(value: str) -> datetime:
    # AT Protocol timestamps are ISO 8601, often ending in "Z"
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _post_url(post: PostView) -> str:
    rkey = post.uri.split("/")[-1]
    return f"https://bsky.app/profile/{post.author.handle}/post/{rkey}"


def _uri_to_key(uri: str) -> str:
    # at://<did>/app.bsky.feed.post/<rkey> -> post|bsky:<did>|<rkey>
    did, _, rkey = uri.removeprefix("at://").split("/", 2)
    return f"post|bsky:{did}|{rkey}"


def _links_from_record(record) -> list[Link]:
    links: list[Link] = []

    # Reply: point at the parent post
    if getattr(record, "reply", None) is not None:
        parent_uri = record.reply.parent.uri
        links.append(Link(type="reply", target=_uri_to_key(parent_uri)))

    # Quote: an embed that references another post
    embed = getattr(record, "embed", None)
    if embed is not None and getattr(embed, "record", None) is not None:
        quoted = embed.record
        quoted_uri = getattr(quoted, "uri", None)
        if quoted_uri and "/app.bsky.feed.post/" in quoted_uri:
            links.append(Link(type="quote", target=_uri_to_key(quoted_uri)))

    # External URLs from link facets
    for facet in getattr(record, "facets", None) or []:
        for feature in facet.features:
            uri = getattr(feature, "uri", None)
            if uri:
                links.append(Link(type="url", target=uri))

    return links


def _media_from_post(post: PostView) -> list[Media]:
    media: list[Media] = []
    embed = post.embed
    if embed is None:
        return media

    # Images: one Media per image, ref is the blob CID
    images = getattr(embed, "images", None) or []
    if not images and getattr(embed, "media", None) is not None:
        images = getattr(embed.media, "images", None) or []
    for img in images:
        ref = getattr(img, "fullsize", None) or getattr(img, "thumb", None)
        if ref:
            media.append(Media(type="photo", ref=ref))

    # Video: the playlist URL, if present
    video_url = getattr(embed, "playlist", None)
    if video_url is None and getattr(embed, "media", None) is not None:
        video_url = getattr(embed.media, "playlist", None)
    if video_url:
        media.append(Media(type="video", ref=video_url))

    return media


def bluesky_post_to_schema(post: PostView, query_name: str | None = None) -> Post:
    record = post.record
    lang = record.langs[0] if getattr(record, "langs", None) else None

    return Post(
        url=_post_url(post),
        author=f"bsky:{post.author.did}",
        author_name=post.author.display_name or post.author.handle,
        posted_at=_parse_iso(record.created_at),
        scraped_at=datetime.now(timezone.utc),
        lang=lang,
        text=record.text,
        links=_links_from_record(record),
        media=_media_from_post(post),
        raw=post.model_dump(mode="json"),
        views=None,                       # Bluesky doesn't expose view counts
        forwards=post.repost_count,
        is_edit=False,
    )