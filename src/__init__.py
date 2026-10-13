"""
Алиас обратной совместимости: `import src.X` отдает тот же модуль, что `import backtest_strat.X`.

Пакет был переименован из `src` в `backtest_strat`, чтобы его можно было ставить как библиотеку без конфликта с
папкой `src/` в чужих проектах. Алиас живет только в репозитории (в wheel не входит) и нужен, пока старые тесты/скрипты
не переведены на новое имя. Новый код должен импортировать `backtest_strat`.
"""
import importlib
import importlib.abc
import importlib.machinery
import sys

_OLD, _NEW = "src", "backtest_strat"


class _AliasFinder(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith(_OLD + "."):
            return importlib.machinery.ModuleSpec(fullname, self)
        return None

    def create_module(self, spec):
        real = importlib.import_module(_NEW + spec.name[len(_OLD):])
        sys.modules[spec.name] = real          # один и тот же объект модуля => patch()/состояние общие
        return real

    def exec_module(self, module):
        pass


if not any(isinstance(f, _AliasFinder) for f in sys.meta_path):
    sys.meta_path.insert(0, _AliasFinder())

# `import src` как пакет с подмодулями из нового пакета
import backtest_strat as _pkg  # noqa: E402

__path__ = list(_pkg.__path__)
