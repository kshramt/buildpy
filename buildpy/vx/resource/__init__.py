import abc
import fcntl
import functools
import hashlib
import json
import os
import shutil
import threading
import time

import botocore.exceptions
import google.cloud.exceptions

from .._log import logger
from .. import _tval
from .. import _convenience
from .. import exception


BUF_SIZE = 65536


class Resource(abc.ABC):
    @classmethod
    @abc.abstractmethod
    def rm(cls, uri, credential):
        pass

    @classmethod
    @abc.abstractmethod
    def mtime_of(cls, uri, credential, use_hash, resource_hash_dir):
        pass

    @classmethod
    @abc.abstractmethod
    def _check_uri(cls, uri):
        pass


class LocalFile(Resource):

    exceptions = (OSError,)
    scheme = "file"

    @classmethod
    def rm(cls, uri, credential):
        puri = cls._check_uri(uri)
        try:
            return os.remove(puri.uri)
        except OSError:
            return shutil.rmtree(puri.uri)

    @classmethod
    def mtime_of(cls, uri, credential, use_hash, resource_hash_dir):
        """
        == Returns
        * min(uri_time, cache_time)
        """
        puri = _convenience.uriparse(uri)
        t_uri = os.path.getmtime(puri.uri)
        if not use_hash:
            return t_uri
        return _min_of_t_uri_and_t_cache(
            t_uri, functools.partial(_hash_of_path, puri.uri), puri, resource_hash_dir
        )

    @classmethod
    def _check_uri(cls, uri):
        """
        * /path/to
        * file:///path/to
        * file://localhost/path/to
        """
        puri = _convenience.uriparse(uri)
        assert puri.scheme == cls.scheme, puri
        assert puri.netloc == "localhost", puri
        return puri


class BigQuery(Resource):

    exceptions = (google.cloud.exceptions.NotFound,)
    scheme = "bq"
    _tls = threading.local()

    @classmethod
    def rm(cls, uri, credential):
        puri = cls._check_uri(uri)
        project, dataset, table = puri.netloc.split(".", 2)
        client = cls._client_of(credential, project)
        return client.delete_table(client.dataset(dataset).table(table))

    @classmethod
    def mtime_of(cls, uri, credential, use_hash, resource_hash_dir):
        puri = cls._check_uri(uri)
        project, dataset, table = puri.netloc.split(".", 2)
        client = cls._client_of(credential, project)
        table = client.get_table(client.dataset(dataset).table(table))
        t_uri = table.modified.timestamp()
        # BigQuery does not provide a hash
        return t_uri

    @classmethod
    def _client_of(cls, credential, project):
        import google.cloud.bigquery

        if not hasattr(cls._tls, "cache"):
            cls._tls.cache = dict()
        key = (credential, project)
        if key not in cls._tls.cache:
            if credential is None:
                # GOOGLE_APPLICATION_CREDENTIALS
                cls._tls.cache[key] = google.cloud.bigquery.Client(project=project)
            else:
                cls._tls.cache[
                    key
                ] = google.cloud.bigquery.Client.from_service_account_json(
                    credential, project=project
                )
        return cls._tls.cache[key]

    @classmethod
    def _check_uri(cls, uri):
        """
        bq://project.dataset.table
        """
        puri = _convenience.uriparse(uri)
        assert puri.scheme == cls.scheme, puri
        assert puri.params == "", puri
        assert puri.query == "", puri
        assert puri.fragment == "", puri
        return puri


class GoogleCloudStorage(Resource):

    exceptions = (exception.NotFound,)
    scheme = "gs"
    _tls = threading.local()

    @classmethod
    def rm(cls, uri, credential):
        puri = cls._check_uri(uri)
        client = cls._client_of(credential)
        bucket = client.get_bucket(puri.netloc)
        # Ignoring generation
        blob = bucket.get_blob(puri.path[1:])
        if blob is None:
            raise exception.NotFound(uri)
        return blob.delete()

    @classmethod
    def mtime_of(cls, uri, credential, use_hash, resource_hash_dir):
        puri = cls._check_uri(uri)
        client = cls._client_of(credential)
        bucket = client.get_bucket(puri.netloc)
        # Ignoring generation
        blob = bucket.get_blob(puri.path[1:])
        if blob is None:
            raise exception.NotFound(uri)
        t_uri = blob.time_created.timestamp()
        if not use_hash:
            return t_uri
        return _min_of_t_uri_and_t_cache(
            t_uri, lambda: blob.md5_hash, puri, resource_hash_dir
        )

    @classmethod
    def _client_of(cls, credential):
        import google.cloud.storage

        if not hasattr(cls._tls, "cache"):
            cls._tls.cache = dict()
        key = (credential,)
        if key not in cls._tls.cache:
            if credential is None:
                # GOOGLE_APPLICATION_CREDENTIALS
                cls._tls.cache[key] = google.cloud.storage.Client()
            else:
                cls._tls.cache[
                    key
                ] = google.cloud.storage.Client.from_service_account_json(credential)
        return cls._tls.cache[key]

    @classmethod
    def _check_uri(cls, uri):
        """
        gs://bucket/blob
        """
        puri = _convenience.uriparse(uri)
        assert puri.scheme == cls.scheme, puri
        assert puri.params == "", puri
        assert puri.query == "", puri
        assert puri.fragment == "", puri
        return puri


class S3(Resource):

    exceptions = (botocore.exceptions.ClientError,)
    scheme = "s3"
    _tls = threading.local()

    @classmethod
    def rm(cls, uri, credential):
        puri = cls._check_uri(uri)
        client = cls._client_of(credential)
        return client.delete_object(Bucket=puri.netloc, Key=puri.path[1:])

    @classmethod
    def mtime_of(cls, uri, credential, use_hash, resource_hash_dir):
        puri = cls._check_uri(uri)
        client = cls._client_of(credential)
        head = client.head_object(Bucket=puri.netloc, Key=puri.path[1:])
        t_uri = head["LastModified"].timestamp()
        if not use_hash:
            return t_uri
        return _min_of_t_uri_and_t_cache(
            t_uri, lambda: head["ETag"], puri, resource_hash_dir
        )

    @classmethod
    def _client_of(cls, credential):
        import boto3

        if not hasattr(cls._tls, "cache"):
            cls._tls.cache = dict()
        key = (credential,)
        if key not in cls._tls.cache:
            if credential is None:
                # AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY
                # ~/.aws/credentials
                aws_access_key_id, aws_secret_access_key = None, None
            else:
                aws_access_key_id, aws_secret_access_key = credential
            cls._tls.cache[key] = boto3.session.Session(
                aws_access_key_id=aws_access_key_id,
                aws_secret_access_key=aws_secret_access_key,
            ).client("s3")
        return cls._tls.cache[key]

    @classmethod
    def _check_uri(cls, uri):
        """
        s3://bucket/object
        """
        puri = _convenience.uriparse(uri)
        assert puri.scheme == cls.scheme, puri
        assert puri.params == "", puri
        assert puri.query == "", puri
        assert puri.fragment == "", puri
        return puri


of_scheme = _tval.TDict(dict())
exceptions = ()


def register(resource):
    global exceptions
    of_scheme[resource.scheme] = resource()
    exceptions += resource.exceptions


register(LocalFile)
register(BigQuery)
register(GoogleCloudStorage)
register(S3)


def _min_of_t_uri_and_t_cache(t_uri, force_hash, puri, resource_hash_dir):
    """
    min(uri_time, cache_time)
    """
    assert puri.uri, puri
    cache_path = _convenience.jp(
        resource_hash_dir, puri.scheme, puri.netloc, os.path.abspath(puri.uri)
    )
    try:
        cache_path_stat = os.stat(cache_path)
    except OSError:
        h_path = force_hash()
        _dump_hash_time_cache(cache_path, t_uri, h_path)
        return t_uri

    try:
        t_cache, h_cache = _load_hash_time_cache(cache_path)
    except (OSError, KeyError):
        h_path = force_hash()
        _dump_hash_time_cache(cache_path, t_uri, h_path)
        return t_uri

    if cache_path_stat.st_mtime > t_uri:
        return t_cache
    else:
        h_path = force_hash()
        if h_path == h_cache:
            t_now = time.time()
            os.utime(cache_path, (t_now, t_now))
            return t_cache
        else:
            _dump_hash_time_cache(cache_path, t_uri, h_path)
            return t_uri


def _dump_hash_time_cache(cache_path, t_path, h_path):
    logger.debug(cache_path)
    _convenience.mkdir(_convenience.dirname(cache_path))
    with open(cache_path, "w") as fp:
        fcntl.flock(fp, fcntl.LOCK_EX)
        json.dump(dict(t=t_path, h=h_path), fp)


def _load_hash_time_cache(cache_path):
    with open(cache_path, "r") as fp:
        fcntl.flock(fp, fcntl.LOCK_EX)
        data = json.load(fp)
    return data["t"], data["h"]


def _hash_of_path(path, buf_size=BUF_SIZE):
    logger.debug(path)
    buf = bytearray(buf_size)
    h = hashlib.sha1(b"")
    with open(path, "rb") as fp:
        while True:
            n = fp.readinto(buf)
            if n <= 0:
                break
            elif n < buf_size:
                h.update(buf[:n])
            else:
                h.update(buf)
    return h.hexdigest()
