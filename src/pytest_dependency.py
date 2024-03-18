"""$DOC"""

__version__ = "$VERSION"

from typing import (
    cast,
    TYPE_CHECKING,
)

if TYPE_CHECKING:
    from typing import (
        List,
        MutableMapping,
        MutableSequence,
        Sequence,
        Set,
        Union,
    )

import logging
import pytest

logger = logging.getLogger(__name__)

_automark = False
_ignore_unknown = False


class DependencyItemStatus(object):
    """Status of a test item in a dependency manager.
    """

    Phases = ('setup', 'call', 'teardown')

    def __init__(self):
        self.results = { w:None for w in self.Phases }

    def __str__(self):
        l = ["%s: %s" % (w, self.results[w]) for w in self.Phases]
        return "Status(%s)" % ", ".join(l)

    def addResult(self, rep):
        self.results[rep.when] = rep.outcome

    def isSuccess(self):
        return list(self.results.values()) == ['passed', 'passed', 'passed']


class DependencyManager(object):
    """Dependency manager, stores the results of tests.
    """

    ScopeCls = {
        'session': pytest.Session,
        'package': pytest.Package,
        'module': pytest.Module,
        'class': pytest.Class,
    }

    @classmethod
    def getManager(cls, item, scope):
        """Get the DependencyManager object from the node at scope level.
        Create it, if not yet present.
        """
        node = item.getparent(cls.ScopeCls[scope])
        if not node:
            return None
        if not hasattr(node, 'dependencyManager'):
            node.dependencyManager = cls(scope)
        return node.dependencyManager

    def __init__(self, scope):
        self.results = {}
        self.scope = scope

    def addResult(self, item, name, rep):
        if not name:
            # Old versions of pytest used to add an extra "::()" to
            # the node ids of class methods to denote the class
            # instance.  This has been removed in pytest 4.0.0.
            nodeid = item.nodeid.replace("::()::", "::")
            if self.scope == 'session' or self.scope == 'package':
                name = nodeid
            elif self.scope == 'module':
                name = nodeid.split("::", 1)[1]
            elif self.scope == 'class':
                name = nodeid.split("::", 2)[2]
            else:
                raise RuntimeError("Internal error: invalid scope '%s'"
                                   % self.scope)
        status = self.results.setdefault(name, DependencyItemStatus())
        logger.debug("register %s %s %s in %s scope",
                     rep.when, name, rep.outcome, self.scope)
        status.addResult(rep)

    def checkDepend(self, depends, item):
        logger.debug("check dependencies of %s in %s scope ...",
                     item.name, self.scope)
        for i in depends:
            if i in self.results:
                if self.results[i].isSuccess():
                    logger.debug("... %s succeeded", i)
                    continue
                else:
                    logger.debug("... %s has not succeeded", i)
            else:
                logger.debug("... %s is unknown", i)
                if _ignore_unknown:
                    continue
            logger.info("skip %s because it depends on %s", item.name, i)
            pytest.skip("%s depends on %s" % (item.name, i))


def depends(request, other, scope='module'):
    """Add dependency on other test.

    Call pytest.skip() unless a successful outcome of all of the tests in
    other has been registered previously.  This has the same effect as
    the `depends` keyword argument to the :func:`pytest.mark.dependency`
    marker.  In contrast to the marker, this function may be called at
    runtime during a test.

    :param request: the value of the `request` pytest fixture related
        to the current test.
    :param other: dependencies, a list of names of tests that this
        test depends on.  The names of the dependencies must be
        adapted to the scope.
    :type other: iterable of :class:`str`
    :param scope: the scope to search for the dependencies.  Must be
        either `'session'`, `'package'`, `'module'`, or `'class'`.
    :type scope: :class:`str`

    .. versionadded:: 0.2

    .. versionchanged:: 0.5.0
        the scope parameter has been added.
    """
    item = request.node
    manager = DependencyManager.getManager(item, scope=scope)
    manager.checkDepend(other, item)


def pytest_addoption(parser):
    parser.addini("automark_dependency", 
                  "Add the dependency marker to all tests automatically", 
                  type="bool", default=False)
    parser.addoption("--ignore-unknown-dependency", 
                     action="store_true", default=False, 
                     help="ignore dependencies whose outcome is not known")


def pytest_configure(config):
    global _automark, _ignore_unknown
    _automark = config.getini("automark_dependency")
    _ignore_unknown = config.getoption("--ignore-unknown-dependency")
    config.addinivalue_line("markers", 
                            "dependency(name=None, depends=[]): "
                            "mark a test to be used as a dependency for "
                            "other tests or to depend on other tests.")


@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """Store the test outcome if this item is marked "dependency".
    """
    outcome = yield
    marker = item.get_closest_marker("dependency")
    if marker is not None or _automark:
        rep = outcome.get_result()
        name = marker.kwargs.get('name') if marker is not None else None
        for scope in DependencyManager.ScopeCls:
            manager = DependencyManager.getManager(item, scope=scope)
            if (manager):
                manager.addResult(item, name, rep)


def pytest_runtest_setup(item):
    """Check dependencies if this item is marked "dependency".
    Skip if any of the dependencies has not been run successfully.
    """
    marker = item.get_closest_marker("dependency")
    if marker is not None:
        depends = marker.kwargs.get('depends')
        if depends:
            scope = marker.kwargs.get('scope', 'module')
            manager = DependencyManager.getManager(item, scope=scope)
            manager.checkDepend(depends, item)

import inspect
from _pytest.compat import (
    get_real_func,
    is_generator,
)

FirstPassCollect: "MutableMapping[str, Sequence[pytest.Item]]" = dict()

@pytest.hookimpl(tryfirst=True)
def pytest_pycollect_makeitem(
    collector: "Union[pytest.Module, pytest.Class]", name: "str", obj: "object"
) -> "Union[None, pytest.Item, pytest.Collector, List[Union[pytest.Item, pytest.Collector]]]":
    assert isinstance(collector, (pytest.Class, pytest.Module)), type(collector)
    # Nothing was collected elsewhere, let's do it here.
    if collector.istestfunction(obj, name):
        # mock seems to store unbound methods (issue473), normalize it.
        obj = getattr(obj, "__func__", obj)
        # We need to try and unwrap the function if it's a functools.partial
        # or a functools.wrapped.
        # We mustn't if it's been wrapped with mock.patch (python 2 only).
        if (inspect.isfunction(obj) or inspect.isfunction(get_real_func(obj))) and getattr(obj, "__test__", True):
            if not is_generator(obj):
                retval = list(collector._genfunctions(name, obj))
                FirstPassCollect[name] = cast("Sequence[pytest.Item]", retval)
                return cast("List[Union[pytest.Item, pytest.Collector]]", retval)
    return None

@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(config: "pytest.Config", items: "MutableSequence[pytest.Item]") -> "None":
    # Borrowed from https://github.com/RKrahl/pytest-dependency/issues/37#issuecomment-1589629688
    # It is not perfect, because it borks fixtures
    seen: "MutableMapping[str, Set[str]]" = dict()
    new_items: "MutableSequence[pytest.Item]" = list()
    
    def dfs(item: "pytest.Item", do_collect: "bool" = False) -> "Set[str]":
        if item.name in seen:
            return seen[item.name]
        
        if isinstance(item, pytest.Function) and len(item.name) > len(item.originalname):
            postfix = item.name[len(item.originalname):]
        else:
            postfix = ""
        do_append = True
        new_dependencies: "Set[str]" = set()
        for marker in item.iter_markers(name="dependency"):
            if do_collect or marker.kwargs.get("collect"):
                new_dependencies = set()
                dependencies = set(marker.kwargs.get("depends", []))
                dfs_funs: "MutableSequence[pytest.Item]" = []
                for dependency in dependencies:
                    funs = FirstPassCollect.get(dependency)
                    new_dependency = dependency
                    if funs is not None:
                        for fun in funs:
                            dependency_alt = dependency + postfix
                            # Could find the dependency
                            if fun.name in (dependency, dependency_alt):
                                if fun.name != dependency:
                                    new_dependency = dependency_alt
                                dfs_funs.append(fun)
                                break
                        #for fun in funs:
                        #    fun = pytest.Function.from_parent(
                        #        name=basefun.name,
                        #        parent=basefun.parent,
                        #        callspec=basefun.callspec,
                        #        callobj=basefun.obj,
                        #        keywords=basefun.keywords,
                        #        fixtureinfo=basefun._fixtureinfo,
                        #        originalname=basefun.originalname,
                        #    )

                    # Always add, so dependencies to be skipped can be still detected
                    new_dependencies.add(new_dependency)
                    #    fun = pytest.Function.from_parent(name=dependency, parent=item.parent)
                    #    # fun.setup()
                    #    dfs(fun, True)
                    #fun = pytest.Function.from_parent(name=dependency, parent=item.parent)
                    ## fun.setup()
                    #dfs(fun)
                # Only when all the dependencies could be resolved is when
                # we are going to the next level
                if len(dfs_funs) == len(new_dependencies):
                    for fun in dfs_funs:
                        new_dependencies.update(dfs(fun, do_collect=True))
                else:
                    do_append = False
                marker.kwargs["depends"] = list(new_dependencies)   # type: ignore[index]
                
        if do_append:
            new_items.append(item)
        seen[item.name] = new_dependencies
        return new_dependencies
    
    for item in items:
        dfs(item)

    old_names = set(item.name for item in items)
    new_names = [item.name for item in new_items if item.name not in old_names]
    if new_names:
        logger.debug("Un-deselected:", *new_names)

    items[:] = new_items
