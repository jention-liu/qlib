"""追踪三层筛选的数据流，打印每步大小"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

# Monkey-patch to suppress tushare calls and side effects, just trace sizes
import screening_logic as sl

orig_run_screening = sl.run_screening
orig_layer1 = sl._layer1_filter_v2
orig_layer2 = sl._layer2_filter_v2

def wrap(fn, name):
    def wrapper(*a, **kw):
        result = fn(*a, **kw)
        print(f"  [{name}] input={a[0].shape if a and hasattr(a[0], 'shape') else 'N/A'}, output={result.shape}")
        return result
    return wrapper

sl.run_screening = wrap(orig_run_screening, "run_screening")
sl._layer1_filter_v2 = wrap(orig_layer1, "_layer1_filter_v2")
sl._layer2_filter_v2 = wrap(orig_layer2, "_layer2_filter_v2")

# Now trace _run_all_layers line by line from source
import inspect
src = inspect.getsource(sl._run_all_layers)
# Print just the key lines
for i, line in enumerate(src.split('\n')):
    if any(kw in line for kw in ['white', 'layer0_pass', 'layer1_pass', 'df2', 'layer2_filter', 'output_cols', 'result', 'gray', 'excluded']):
        print(f"  LINE: {line.strip()}")
