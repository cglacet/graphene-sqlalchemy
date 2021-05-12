import logging

import sqlalchemy
from promise import dataloader, promise
from sqlalchemy.orm import Session, strategies
from sqlalchemy.orm.query import QueryContext

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)


def get_batch_resolver(relationship_prop):

    # Cache this across `batch_load_fn` calls
    # This is so SQL string generation is cached under-the-hood via `bakery`
    selectin_loader = strategies.SelectInLoader(
        relationship_prop, (("lazy", "selectin"),)
    )

    class RelationshipLoader(dataloader.DataLoader):
        cache = False

        def batch_load_fn(self, parents):  # pylint: disable=method-hidden
            """
            Batch loads the relationships of all the parents as one SQL statement.

            There is no way to do this out-of-the-box with SQLAlchemy but
            we can piggyback on some internal APIs of the `selectin`
            eager loading strategy. It's a bit hacky but it's preferable
            than re-implementing and maintainnig a big chunk of the `selectin`
            loader logic ourselves.

            The approach here is to build a regular query that
            selects the parent and `selectin` load the relationship.
            But instead of having the query emits 2 `SELECT` statements
            when callling `all()`, we skip the first `SELECT` statement
            and jump right before the `selectin` loader is called.
            To accomplish this, we have to construct objects that are
            normally built in the first part of the query in order
            to call directly `SelectInLoader._load_for_path`.

            TODO Move this logic to a util in the SQLAlchemy repo as per
              SQLAlchemy's main maitainer suggestion.
              See https://git.io/JewQ7
            """
            child_mapper = relationship_prop.mapper
            parent_mapper = relationship_prop.parent
            session = Session.object_session(parents[0])

            # These issues are very unlikely to happen in practice...
            for parent in parents:
                # assert parent.__mapper__ is parent_mapper
                # All instances must share the same session
                assert session is Session.object_session(parent)
                # The behavior of `selectin` is undefined if the parent is dirty
                assert parent not in session.dirty

            # Should the boolean be set to False? Does it matter for our purposes?
            states = [(sqlalchemy.inspect(parent), True) for parent in parents]

            # For our purposes, the query_context will only used to get the session
            logger.debug(
                "+++ query_context: parent_mapper.entity = %s , type(parent_mapper) = %s",
                parent_mapper.entity,
                type(parent_mapper),
            )
            logger.debug(
                "*** query_context: session.query = %s, type(session) = %s",
                session.query(parent_mapper.entity),
                type(session),
            )
            query_context = QueryContext(session.query(parent_mapper.entity))

            selectin_loader._load_for_path(
                query_context,
                parent_mapper._path_registry,
                states,
                None,
                child_mapper,
            )

            return promise.Promise.resolve(
                [getattr(parent, relationship_prop.key) for parent in parents]
            )

    loader = RelationshipLoader()

    def resolve(root, info, **args):
        return loader.load(root)

    return resolve
