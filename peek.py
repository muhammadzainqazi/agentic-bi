lines = open('app.py').readlines() 
for i, l in enumerate(lines[48:62], start=49): 
    print(f'{i}: {l}', end='') 
