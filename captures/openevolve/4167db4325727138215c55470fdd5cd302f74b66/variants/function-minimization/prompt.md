# System Prompt

You are an expert programmer specializing in optimization algorithms. Your task is to improve a function minimization algorithm to find the global minimum of a complex function with many local minima. The function is f(x, y) = sin(x) * cos(y) + sin(x*y) + (x^2 + y^2)/20. Focus on improving the search_algorithm function to reliably find the global minimum, escaping local minima that might trap simple algorithms.

# User Message

## Current Program Information
- Fitness: 1.2148
- Feature coordinates:
- Focus areas: - Fitness unchanged at 1.2148
- No feature coordinates
- Consider simplifying - code length exceeds 500 characters

### Last Execution Output

#### stage1_result
```
Found solution at x=-2.0245, y=0.6502 with value=-1.4572
```

#### distance_to_global
```
0.3217
```

#### solution_quality
```
Distance < 0.5: Very close
```

## Program Evolution History
### Previous Attempts

#### Attempt 1
- Changes: Unknown changes
- Metrics: runs_successfully: 1.0000, value_score: 0.9418, distance_score: 0.7566, combined_score: 1.2148
- Outcome: Improvement in all metrics

### Top Performing Programs

#### Program 1 (Score: 1.2148)
```python
## EVOLVE-BLOCK-START
"""Function minimization example for OpenEvolve"""
import numpy as np


def search_algorithm(iterations=1000, bounds=(-5, 5)):
    """
    A simple random search algorithm that often gets stuck in local minima.

    Args:
        iterations: Number of iterations to run
        bounds: Bounds for the search space (min, max)

    Returns:
        Tuple of (best_x, best_y, best_value)
    """
    # Initialize with a random point
    best_x = np.random.uniform(bounds[0], bounds[1])
    best_y = np.random.uniform(bounds[0], bounds[1])
    best_value = evaluate_function(best_x, best_y)

    for _ in range(iterations):
        # Simple random search
        x = np.random.uniform(bounds[0], bounds[1])
        y = np.random.uniform(bounds[0], bounds[1])
        value = evaluate_function(x, y)

        if value < best_value:
            best_value = value
            best_x, best_y = x, y

    return best_x, best_y, best_value


## EVOLVE-BLOCK-END


## This part remains fixed (not evolved)
def evaluate_function(x, y):
    """The complex function we're trying to minimize"""
    return np.sin(x) * np.cos(y) + np.sin(x * y) + (x**2 + y**2) / 20


def run_search():
    x, y, value = search_algorithm()
    return x, y, value


if __name__ == "__main__":
    x, y, value = run_search()
    print(f"Found minimum at ({x}, {y}) with value {value}")

```
Key features: Metric: runs_successfully (1.0000), Metric: value_score (0.9418), Metric: distance_score (0.7566), Metric: combined_score (1.2148)



## Current Program
```python
## EVOLVE-BLOCK-START
"""Function minimization example for OpenEvolve"""
import numpy as np


def search_algorithm(iterations=1000, bounds=(-5, 5)):
    """
    A simple random search algorithm that often gets stuck in local minima.

    Args:
        iterations: Number of iterations to run
        bounds: Bounds for the search space (min, max)

    Returns:
        Tuple of (best_x, best_y, best_value)
    """
    # Initialize with a random point
    best_x = np.random.uniform(bounds[0], bounds[1])
    best_y = np.random.uniform(bounds[0], bounds[1])
    best_value = evaluate_function(best_x, best_y)

    for _ in range(iterations):
        # Simple random search
        x = np.random.uniform(bounds[0], bounds[1])
        y = np.random.uniform(bounds[0], bounds[1])
        value = evaluate_function(x, y)

        if value < best_value:
            best_value = value
            best_x, best_y = x, y

    return best_x, best_y, best_value


## EVOLVE-BLOCK-END


## This part remains fixed (not evolved)
def evaluate_function(x, y):
    """The complex function we're trying to minimize"""
    return np.sin(x) * np.cos(y) + np.sin(x * y) + (x**2 + y**2) / 20


def run_search():
    x, y, value = search_algorithm()
    return x, y, value


if __name__ == "__main__":
    x, y, value = run_search()
    print(f"Found minimum at ({x}, {y}) with value {value}")

```

## Task
Suggest improvements to the program that will improve its FITNESS SCORE.
The system maintains diversity across these dimensions: complexity, diversity
Different solutions with similar fitness but different features are valuable.

You MUST use the exact SEARCH/REPLACE diff format shown below to indicate changes:

<<<<<<< SEARCH
## Original code to find and replace (must match exactly)
=======
## New replacement code
>>>>>>> REPLACE

Example of valid diff format:
<<<<<<< SEARCH
for i in range(m):
    for j in range(p):
        for k in range(n):
            C[i, j] += A[i, k] * B[k, j]
=======
## Reorder loops for better memory access pattern
for i in range(m):
    for k in range(n):
        for j in range(p):
            C[i, j] += A[i, k] * B[k, j]
>>>>>>> REPLACE

You can suggest multiple changes. Each SEARCH section must exactly match code in the current program.
Be thoughtful about your changes and explain your reasoning thoroughly.

IMPORTANT: Do not rewrite the entire program - focus on targeted improvements.

# Tools

_No tools captured._
