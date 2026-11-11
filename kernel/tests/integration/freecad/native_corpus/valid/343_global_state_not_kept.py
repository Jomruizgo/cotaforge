# expect: ok
# setup: 066_helper_function_plate
# about: A name defined in one step is not visible in the next
try:
    leftover
except NameError:
    print("fresh namespace per step")
