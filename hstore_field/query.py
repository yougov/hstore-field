import datetime
import numbers
from django.db.models.fields import FieldDoesNotExist
from django.utils import tree
from django.core.exceptions import FieldError
try:
    from django.db.models.sql.constants import LOOKUP_SEP
except:
    from django.db.models.constants import LOOKUP_SEP


class HStoreConstraint():

    value_operators = {'exact': '=', 'iexact': '=', 'in': 'IN', 'lt': '<', 'lte': '<=', 'gt': '>', 'gte': '>='}

    def __init__(self, alias, field, value, lookup_type, key=None):

        self.lvalue = '%s'
        self.alias = alias
        self.field = field
        self.values = [value]

        if lookup_type == 'contains':
            if isinstance(value, basestring):
                self.operator = '?'
            elif isinstance(value, (list, tuple)):
                self.operator = '?&'
                self.values = [list(value)]
            else:
                raise ValueError('invalid value %r' % value)
        elif lookup_type in self.value_operators:
            self.operator = self.value_operators[lookup_type]
            if self.operator == 'IN':
                test_value = value[0] if len(value) > 0 else ''
                self.values = [tuple(value)]
            else:
                test_value = value
            if isinstance(test_value, datetime.datetime):
                cast_type = 'timestamp'
            elif isinstance(test_value, datetime.date):
                cast_type = 'date'
            elif isinstance(test_value, datetime.time):
                cast_type = 'time'
            elif isinstance(test_value, int):
                cast_type = 'integer'
            elif isinstance(test_value, numbers.Number):
                cast_type = 'double precision'
            elif isinstance(test_value, basestring):
                cast_type = None
            else:
                raise ValueError('invalid value %r' % test_value)
            if cast_type:
                self.lvalue = "CAST(NULLIF(%%s->'%s','') AS %s)" % (key, cast_type)
            elif lookup_type == 'iexact':
                self.lvalue = "lower(%%s->'%s')" % key
                self.values = [value.lower()]
            elif lookup_type == 'in' and not value:
                self.operator = '?'
                self.values = [key]
            else:
                self.lvalue = "%%s->'%s'" % key
        else:
            raise TypeError('invalid lookup type')

    def sql_for_column(self, qn, connection):
        if self.alias:
            return '%s.%s' % (qn(self.alias), qn(self.field))
        else:
            return qn(self.field)

    def as_sql(self, qn=None, connection=None):
        lvalue = self.lvalue % self.sql_for_column(qn, connection)
        expr = '%s %s %%s' % (lvalue, self.operator)
        return (expr, self.values)


class HQ(tree.Node):

    AND = 'AND'
    OR = 'OR'
    default = AND
    query_terms = ['exact', 'iexact', 'lt', 'lte', 'gt', 'gte', 'in', 'contains']

    def __init__(self, **kwargs):
        super(HQ, self).__init__(children=kwargs.items())

    def _combine(self, other, conn):
        if not isinstance(other, HQ):
            raise TypeError(other)
        obj = type(self)()
        obj.add(self, conn)
        obj.add(other, conn)
        return obj

    def __or__(self, other):
        return self._combine(other, self.OR)

    def __and__(self, other):
        return self._combine(other, self.AND)

    def __invert__(self):
        obj = type(self)()
        obj.add(self, self.AND)
        obj.negate()
        return obj

    def add_to_query(self, query, used_aliases):
        self.add_to_node(query.where, query, used_aliases)

    def add_to_node(self, where_node, query, used_aliases):
        for child in self.children:
            if  isinstance(child, HQ):
                node = query.where_class()
                child.add_to_node(node, query, used_aliases)
                where_node.add(node, self.connector)
            else:
                field, value = child
                parts = field.split(LOOKUP_SEP)
                if not parts:
                    raise FieldError("Cannot parse keyword query %r" % field)
                lookup_type = self.query_terms[0]  # Default lookup type
                num_parts = len(parts)
                if len(parts) > 1 and parts[-1] in self.query_terms:
                    # Traverse the lookup query to distinguish related fields from
                    # lookup types.
                    lookup_model = query.model
                    for counter, field_name in enumerate(parts):
                        try:
                            lookup_field = lookup_model._meta.get_field(field_name)
                        except FieldDoesNotExist:
                            # Not a field. Bail out.
                            lookup_type = parts.pop()
                            break
                        # Unless we're at the end of the list of lookups, let's attempt
                        # to continue traversing relations.
                        if (counter + 1) < num_parts:
                            try:
                                lookup_model = lookup_field.rel.to
                            except AttributeError:
                                # Not a related field. Bail out.
                                lookup_type = parts.pop()
                                break
                if lookup_type == 'contains':
                    key = None
                else:
                    key = parts[-1]
                    parts = parts[:-1]
                opts = query.get_meta()
                alias = query.get_initial_alias()
                field, target, opts, join_list, last, extra = query.setup_joins(parts, opts, alias, True)
                col, alias, join_list = query.trim_joins(target, join_list, last, False, False)
                where_node.add(HStoreConstraint(alias, col, value, lookup_type, key), self.connector)
        if self.negated:
            where_node.negate()


def add_hstore(queryset, field, key, name=None):
    assert queryset.query.can_filter(), "Cannot change a query once a slice has been taken"
    name = name or key
    clone = queryset._clone()
    clone.query.add_extra({name: "%s -> '%s'" % (field, key)}, None, None, None, None, None)
    return clone
