import pookiedb


class Tag(pookiedb.Model):
    name = pookiedb.CharField(max_length=50, unique=True)
    slug = pookiedb.SlugField(unique=True)

    class Meta:
        db_table = "tags"
        ordering = ["name"]


class Author(pookiedb.Model):
    name = pookiedb.CharField(max_length=100)
    email = pookiedb.EmailField(unique=True)
    bio = pookiedb.TextField(null=True, blank=True)
    website = pookiedb.URLField(null=True, blank=True)
    joined = pookiedb.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "authors"
        ordering = ["-joined"]

    def __str__(self):
        return self.name


class Post(pookiedb.Model):
    title = pookiedb.CharField(max_length=200)
    slug = pookiedb.SlugField(unique=True)
    body = pookiedb.TextField()
    author = pookiedb.ForeignKey(Author, on_delete=pookiedb.CASCADE, related_name="posts")
    tags = pookiedb.ManyToManyField(Tag, related_name="posts")
    published = pookiedb.BooleanField(default=False)
    view_count = pookiedb.IntegerField(default=0)
    metadata = pookiedb.JSONField(null=True)
    created_at = pookiedb.DateTimeField(auto_now_add=True)
    updated_at = pookiedb.DateTimeField(auto_now=True)

    class Meta:
        db_table = "posts"
        ordering = ["-created_at"]
        unique_together = [["author", "slug"]]


class UserProfile(pookiedb.Model):
    author = pookiedb.OneToOneField(Author, on_delete=pookiedb.CASCADE)
    avatar_url = pookiedb.URLField(null=True)
    preferences = pookiedb.JSONField(default=dict)

    class Meta:
        db_table = "user_profiles"
