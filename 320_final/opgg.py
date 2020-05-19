# This file is our script to train a machine learning network to guess League of
# Legends Game outcomes, isn't technology wonderful

import requests,json
import time
from bs4 import BeautifulSoup
import re
import urllib
from signal import signal, SIGINT
from sys import exit
import traceback
import math
import sqlite3
import random
import os.path
from os import path
import matplotlib.pyplot as plt
import numpy as np
import pandas
from statistics import mean,stdev
from scipy.stats import norm
import calendar
import time

############
# File I/O #
############

# Read from our resources to map from numbers to champion names
def read_champ_mapping():
    map = {}
    if not path.exists("resources/champ_mapping"):
        print("File doesn't exist: resources/champ_mapping")
        exit(0)
    with open("resources/champ_mapping") as f:
        for line in f:
            vals = line.strip().split(":")
            map[int(vals[0])] = vals[1]
    return map

def read_damage_types():
    map = {}
    if not path.exists("resources/damage"):
        print("File doesn't exist: resources/damage")
        exit(0)
    with open("resources/damage") as f:
        for line in f:
            vals = line.strip().split(":")
            map[vals[0]] = vals[1]
    return map

# Build Map from names to winrate
def read_from_file():
    champions = {}
    with open("resources/winrates") as f:
        for line in f:
            vals = line.split(":")
            champions[vals[0]] = []
            for number in range(1,len(vals)):
                stuff = vals[number].strip().replace('(','').replace(')','').split(',')
                role = stuff[0]
                winrate = stuff[1].strip()
                #print("{}:{}".format(role,winrate))
                champions[vals[0]].append((role,winrate))
    return champions

####################
# Winrate Updating #
####################

# Go to every champion page for opgg, for every role, if one exists then we pull the winrate,
# if not we just ignore it and move on
def update_wr():
    roles = ["top","jungle","mid","bot","support"]
    if not path.exists("resources/champcopy"):
        print("File doesn't exist: resources/champcopy")
        exit(0)
    with open("resources/champcopy") as f:
        with open("resources/winrates","w+") as writer:
            for champion in f:
                if champion != "":
                    champion = champion.strip()
                    # Request the page for each champion for each role
                    count = 0 # Keep track of how many roles we track for each champion
                    winrates = [] # Keep track of per role winrate
                    for role in roles:
                        element = "None"
                        url = 'https://na.op.gg/champion/{}/statistics/{}'.format(champion,role)
                        data = requests.get(url)
                        data = data.text
                        # Parse the html we get back and search for the element we want (winrate)
                        soup = BeautifulSoup(data,"html.parser")
                        if soup.find('div', attrs={'class': "champion-stats-trend-rate"}) != None:
                            element = soup.find('div', attrs={'class': "champion-stats-trend-rate"}).text.strip().replace("%","")
                            if element == "" or element == "%":
                                winrates.append((role,"None"))
                                continue
                            winrates.append((role,element))
                            print(element)
                            count += 1
                        else:
                            winrates.append((role,"None"))

                    # If we didn't have any valid winrates, give it a default winrate, shouldn't cause things to fluctuate
                    #if count == 0:
                    #    writer.write("{}:50.0\n".format(champion,element))
                    # Otherwise fill in by role
                    #else:
                    writer.write("{}:".format(champion))
                    for (i,winrate) in enumerate(winrates):
                        writer.write("{}".format(winrate))
                        if i < len(winrates) - 1:
                            writer.write(":".format(winrate))
                    writer.write("\n")
    print("Finished updating winrate")
    return

#################################
# Database and Data Aggregation #
#################################

# Initialize database and return the connection to it
def initialize_database(cursor):
    #statement = "CREATE TABLE games (gameId INTEGER PRIMARY KEY,winner INTEGER,top1 varchar(25),jungle1 varchar(25),middle1 varchar(25),bottom1 varchar(25),support1 varchar(25),top2 varchar(25),jungle2 varchar(25),middle2 varchar(25),bottom2 varchar(25),support2 varchar(25));"
    #cursor.execute(statement)
    #statement = "CREATE TABLE summoners (accountId varchar(50), checked INTEGER);"
    #cursor.execute(statement)

    statement = "SELECT * FROM summoners WHERE accountId = 'W9U-Tzxs9WEMaovax0icYtLUlb9D7D0n0ZcL55TTZ-PV2-I';"
    f = cursor.execute(statement).fetchall()
    if len(f) == 0:
        # Fill in my personal account ID
        statement = "INSERT INTO summoners VALUES ('W9U-Tzxs9WEMaovax0icYtLUlb9D7D0n0ZcL55TTZ-PV2-I',0);"
        cursor.execute(statement)
        #print("inserting")
    return

# Pull data from the api and save it to our sql database
def data_aggregator(key,champion_map,conn,cursor):
    player_ids = []
    total_games = 0
    begin_time = calendar.timegm(time.strptime('May 13, 2020 @ 12:00:00 UTC', '%b %d, %Y @ %H:%M:%S UTC')) * 1000

    while True:
        in_val = input("What is the lower bound on the number of games you would like parsed?\n")

        if in_val.strip().isnumeric():
            in_val = int(in_val)
            break
        else:
            print("Non-numeric Answer, please provide an answer containing only numbers.")

    while True:
        # Grab summoners we haven't checked out yet
        statement = "SELECT accountId FROM summoners WHERE checked = 0;"
        ids = cursor.execute(statement).fetchall()

        for id in ids:
            player_ids.append(id[0])

        # Get the farthest back person
        name = player_ids.pop()
        print("Now on summoner: {}".format(name))

        # Update that we're checking out this players match history
        statement = "UPDATE summoners SET checked = 1 WHERE accountId = '{}';".format(name)
        cursor.execute(statement)
        conn.commit()

        # Request someons list of matches and put it into a JSON body
        req = requests.get("https://na1.api.riotgames.com/lol/match/v4/matchlists/by-account/{}?api_key={}&beginTime={}".format(name,key,begin_time))
        summoner_games = req.json()

        # If something went wrong with our request, just ignore the game and move on
        if req.status_code != 200:
            if req.status_code == 403:
                print("Bad Key")
            else:
                print("Person doesn't exist!")
            player_ids.append(name)
            continue

        # Keep track of the number of games we've seen, just for now we're maxing at 1000
        if total_games > in_val:
            break

        # Go through the last games of a given summoner
        for (i,games) in enumerate(summoner_games["matches"]):
            # Keep track of the teams of champions
            team_1 = []
            team_2 = []

            # Limit of 100 games per player
            if i > 99:
                break

            # Progress indicator
            if total_games % 100 == 0:
                print("On game {}".format(total_games))

            # Write the match number
            match_number = games["gameId"]

            # Request the game info and put it into a json body
            request = requests.get("https://na1.api.riotgames.com/lol/match/v4/matches/{}?api_key={}".format(match_number,key))
            game = request.json()

            # If we have a bad status code we sleep for a minute to avoid the rate limit
            if request.status_code != 200:
                print("Hit my limit, on game {} going to sleep for a minute".format(total_games))
                time.sleep(90)
                continue

            # If for whatever reason there's no team field we just ignore it but mark
            # that we were there so we don't need to deal with it
            if "teams" not in game.keys():
                games_viewed.append(match_number)
                continue

            statement = "SELECT * FROM games WHERE gameId = {}".format(match_number)
            f = cursor.execute(statement).fetchmany(5)
            # We're see this game before so we don't want to do it again
            if len(f) != 0:
                continue

            # Check the wins here
            if game["teams"][0]["win"] == "Win":
                winner = 1
            else:
                winner = -1

            # Now get the champions and keep track of them
            for participant in game["participants"]:
                champion_name = champion_map[participant["championId"]]
                lane = participant["timeline"]["lane"].lower()
                if int(participant["teamId"]) == 100:
                    team_1.append(champion_name)
                else:
                    team_2.append(champion_name)

            # Add to the set of players we haven't seen yet
            for player in game["participantIdentities"]:
                id = player["player"]["currentAccountId"]
                statement = "SELECT * FROM summoners WHERE accountId = '{}';".format(id)
                output = cursor.execute(statement).fetchall()
                # If we don't have an entry for this summoner yet, we say "we'll get to you later"
                if len(output) == 0:
                    statement = "INSERT INTO summoners VALUES ('{}',0);".format(id)
                    cursor.execute(statement)

            total_games += 1

            if len(team_1) + len(team_2) == 10:
                statement = "INSERT INTO games VALUES ({},{},'{}','{}','{}','{}','{}','{}','{}','{}','{}','{}');".format(game["gameId"],winner,team_1[0],team_1[1],team_1[2],team_1[3],team_1[4],team_2[0],team_2[1],team_2[2],team_2[3],team_2[4])
                cursor.execute(statement)
            conn.commit()
        conn.commit()
    conn.commit()

    print("I looked over {} games total".format(total_games))
    return

############
# Training #
############

def train(winrate_map,champion_map,damage_types,conn,cursor,values):
    pass_number = 1
    winrate_weight = 0.5
    ad_ap_weight = 0.5

    times = 0

    guessed_rates = 0.0

    learning_rate = 0.5

    while times < 100:
        correct = 0
        total = 0

        e_w = 10000000
        norm = 0

        #while e_w > 0.05:
        # Now we begin guessing
        for (i,(gameId,winner,top1,jungle1,middle1,bottom1,support1,top2,jungle2,middle2,bottom2,support2)) in enumerate(values):
            # Build out our lists of teams
            team_1 = [top1,jungle1,middle1,bottom1,support1]
            team_2 = [top2,jungle2,middle2,bottom2,support2]

            guessed = guess(winrate_map,team_1,team_2,winrate_weight,ad_ap_weight,damage_types)

            guessed_winner_val = 0

            for (element,weight) in guessed:
                guessed_winner_val = element * weight

            if guessed_winner_val < 0:
                guessed_winner = -1
            else:
                guessed_winner = 1

            z = calculate_z(guessed)

            sigma = calculate_sigma(z)

            if sigma <= 0.05:
                print(i)
                print("optimal weights: %.4f %.4f" % (winrate_weight,ad_ap_weight))
                return (correct,total)

            norm += (guessed_winner_val - sigma) ** 2

            winrate_weight = update_weights(z, sigma, guessed[0][0], learning_rate, winrate_weight)

            ad_ap_weight = update_weights(z, sigma, guessed[1][0], learning_rate, ad_ap_weight)

            if winner == guessed_winner:
                correct += 1
            total += 1
        learning_rate = learning_rate / 2

        print("On pass {}: Our current weights are:\nwinrate: {}\nad_ap: {}".format(pass_number,winrate_weight,ad_ap_weight))
        print("On this pass we ended up with {} correct out of {} for a correct percentage of {}".format(correct,total,100.0 * correct/total))
        print("%d Correct\n%d Total\n%.1f Percent correct\nThe optimal weights were: %.4f and %.4f" %(correct, total,100.0 * correct / total,winrate_weight,ad_ap_weight))

        times += 1
        guessed_rates += correct / total
    return (correct,total)

def guess_without_training(winrate_map,champion_map,damage_types,conn,cursor,winrate_weight,ad_ap_weight,values):
    pass_number = 1

    times = 0
    guessed_rates = 0.0

    while times < 100:
        correct = 0
        total = 0

        #while e_w > 0.05:
        # Now we begin guessing
        for (gameId,winner,top1,jungle1,middle1,bottom1,support1,top2,jungle2,middle2,bottom2,support2) in values:
            # Build out our lists of teams
            team_1 = [top1,jungle1,middle1,bottom1,support1]
            team_2 = [top2,jungle2,middle2,bottom2,support2]

            guessed = guess(winrate_map,team_1,team_2,winrate_weight,ad_ap_weight,damage_types)

            guessed_winner_val = 0

            for (element,weight) in guessed:
                guessed_winner_val = element * weight

            if guessed_winner_val < 0:
                guessed_winner = -1
            else:
                guessed_winner = 1

            if winner == guessed_winner:
                correct += 1
            total += 1

        #print("On pass {}: Our current weights are:\nwinrate: {}\nad_ap: {}".format(pass_number,winrate_weight,ad_ap_weight))
        #print("On this pass we ended up with {} correct out of {} for a correct percentage of {}".format(correct,total,100.0 * correct/total))
        #print("%d Correct\n%d Total\n%.1f Percent correct\nThe optimal weights were: %.4f and %.4f" %(correct, total,100.0 * correct / total,winrate_weight,ad_ap_weight))

        times += 1
        guessed_rates += correct / total

    return (correct,total)

def guess_only_ad_ap(winrate_map,champion_map,damage_types,conn,cursor,winrate_weight,ad_ap_weight,values):
    pass_number = 1

    times = 0
    guessed_rates = 0.0

    while times < 100:
        correct = 0
        total = 0

        #while e_w > 0.05:
        # Now we begin guessing
        for (gameId,winner,top1,jungle1,middle1,bottom1,support1,top2,jungle2,middle2,bottom2,support2) in values:
            # Build out our lists of teams
            team_1 = [top1,jungle1,middle1,bottom1,support1]
            team_2 = [top2,jungle2,middle2,bottom2,support2]

            guessed = guess(winrate_map,team_1,team_2,winrate_weight,ad_ap_weight,damage_types)

            guessed_winner_val = 0

            for (element,weight) in guessed:
                guessed_winner_val = element * weight

            if guessed_winner_val < 0:
                guessed_winner = -1
            else:
                guessed_winner = 1

            if winner == guessed_winner:
                correct += 1
            total += 1

        #print("On pass {}: Our current weights are:\nwinrate: {}\nad_ap: {}".format(pass_number,winrate_weight,ad_ap_weight))
        #print("On this pass we ended up with {} correct out of {} for a correct percentage of {}".format(correct,total,100.0 * correct/total))
        #print("%d Correct\n%d Total\n%.1f Percent correct\nThe optimal weights were: %.4f and %.4f" %(correct, total,100.0 * correct / total,winrate_weight,ad_ap_weight))

        times += 1
        guessed_rates += correct / total

    return (correct,total)

def calculate_average_wr(winrate_map,team_1,team_2):
    team_1_total = 0
    team_2_total = 0
    for champion in team_1:
        used_winrate = 0.0
        count = 0
        for (position,winrate) in winrate_map[champion]:
            if winrate != "'None'":
                used_winrate = used_winrate + float(winrate.replace("'",""))
                count += 1
        used_winrate = used_winrate / count
        team_1_total += used_winrate

    for champion in team_2:
        used_winrate = 0.0
        count = 0
        for (position,winrate) in winrate_map[champion]:
            if winrate != "'None'":
                used_winrate = used_winrate + float(winrate.replace("'",""))
                count += 1
        used_winrate = used_winrate / count
        team_2_total += used_winrate

    return (team_1_total/5,team_2_total/5)

def calculate_ad_ap(team,damage_map):
    num_ad = 0
    num_ap = 0
    num_tanks = 0
    for champion in team:
        if damage_map[champion] == "AP":
            num_ap += 1
        elif damage_map[champion] == "AD":
            num_ad += 1
        elif damage_map[champion] == "TANK":
            num_tanks += 1
    return (num_ad,num_ap,num_tanks)

# Reminder, team 1 is +1 team 2 is -1
def guess(winrate_map,team_1,team_2,winrate_weight,ad_ap_weight,damage_map):
    (wr1,wr2) = calculate_average_wr(winrate_map,team_1,team_2)
    (ad1,ap1,tank1) = calculate_ad_ap(team_1,damage_map)
    (ad2,ap2,tank2) = calculate_ad_ap(team_2,damage_map)
    ad_ap_value = 0.0
    winrate_value = 0.0

    for i in range(0,tank1):
        ad_ap_value -= 1
    for i in range(0,tank2):
        ad_ap_value += 1

    # We give large value to the other team if the damage spread isn't great
    #if (ad1 == 0 or ap1 == 0) and tank2 > 1:
    #    ad_ap_value += -2 * ad_ap_weight
    #if (ad2 == 0 or ap2 == 0) and tank1 > 1:
    #    ad_ap_value += 2 * ad_ap_weight
    if ad1 == 0 or ap1 == 0:
        ad_ap_value += -1 * ad_ap_weight
    if ad2 == 0 or ap2 == 0:
        ad_ap_value += 1 * ad_ap_weight

    weight_and_values = ((wr1-wr2,winrate_weight),(ad_ap_value,ad_ap_weight))

    return weight_and_values

def calculate_z(weight_and_values):
    z = 0
    for (weight,value) in weight_and_values:
        z += weight * value
    return z

def calculate_sigma(z):
    sigma = 1/(1 + math.exp(-z))
    return sigma

def update_weights(y_values, o_j, x_values, learning_rate, weight_initial):
    d_e = - (y_values - o_j) * o_j * (1 - o_j) * x_values
    weight_new = weight_initial - learning_rate * d_e
    return weight_new

def read_and_graph(file_name):
    if not path.exists(file_name):
        print("File doesn't exist: {}".format(file_name))
        exit(0)
    percentages = []
    with open(file_name) as f:
        for line in f:
            percentages.append(line.split("\t")[2])
    graph(percentages)

############
# Plotting #
############

def graph(data):
    #data = data.sort()
    print(data)

    # setting the ranges and no. of intervals
    range = (0, 100)
    bins = 10

    # plotting a histogram
    plt.hist(data, bins, range, color = 'green',
            histtype = 'bar', rwidth = 0.8)

    # x-axis label
    plt.xlabel('Percentage Correct')
    # frequency label
    plt.ylabel('Frequency')
    # plot title
    plt.title('Percentage Correct 1000 tests')

    # function to show the plot
    plt.show()
    return

def graph_winrate_by_roles():
    top = []
    mid = []
    jungle = []
    bot = []
    supp = []
    with open("resources/winrates") as f:
        for line in f:
            if line != "":
                champ_and_roles = line.replace("'","").split(":")
                for stuff in champ_and_roles:
                    split_up = stuff.replace("(","").replace(")","").split(',')
                    if len(split_up) < 2:
                        continue
                    role = split_up[0]
                    wr = split_up[1].strip()
                    if wr != "None":
                        if role == "top":
                            top.append(float(wr.strip().replace("'","")))
                        elif role == "jungle":
                            jungle.append(float(wr.strip().replace("'","")))
                        elif role == "mid":
                            mid.append(float(wr.strip().replace("'","")))
                        elif role == "bot":
                            bot.append(float(wr.strip().replace("'","")))
                        elif role == "support":
                            supp.append(float(wr.strip().replace("'","")))

    length = max(len(top),len(jungle),len(mid),len(bot),len(supp))

    data = {'Top Lane':top,'Jungle':jungle,'Middle Lane':mid,'Bottom Lane':bot,'Support':supp}

    for (key,value) in data.items():
        if len(value) < length:
            diff = length - len(value)
            value.extend([50.0] * diff)

    frame = pandas.DataFrame(data)
    boxplot = frame.boxplot()
    plt.ylabel('Winrates (Percetage)')
    plt.xlabel('Game Roles')
    plt.title('Winrate Distribution by Game Role')
    plt.show()

    return

def show_histogram():
    bot = []
    with open("resources/winrates") as f:
        for line in f:
            if line != "":
                champ_and_roles = line.replace("'","").split(":")
                for stuff in champ_and_roles:
                    split_up = stuff.replace("(","").replace(")","").split(',')
                    if len(split_up) < 2:
                        continue
                    role = split_up[0]
                    wr = split_up[1].strip()
                    if wr != "None":
                        if role == "top":
                            bot.append(float(wr.strip().replace("'","")))
    mu = mean(bot)
    sigma = stdev(bot)
    #frame = pandas.DataFrame(bot)
    #ax = plt.subplots(1, 1)
    num_bins = 25
    x = np.linspace(min(bot), max(bot), 25)
    plt.plot(x, norm.pdf(x),'r-', lw=5, alpha=0.6, label='norm pdf')
    n, bins, patches = plt.hist(bot,num_bins,rwidth=0.5)
    plt.xticks(np.arange(int(min(bot) - 1), int(max(bot)+2), 1.0))
    #plt.yticks(np.arange(0,4, 1.0))
    plt.ylabel('Frequency Winrate Seen')
    plt.xlabel('Winrate (Percentage)')
    plt.title('Distribution of Top Lane Winrates (25 buckets)')
    plt.show()

def plot_output_correct(output):
    plt.scatter(output.keys(), output.values())
    plt.ylabel('Correct Rate (100 * Correct / Total)')
    plt.xlabel('Number of Times Guessed')
    plt.title('Guessing Winner With Only Winrate')
    plt.xticks(np.arange(0, 9000, 1000))
    plt.show()
    return

def try_for_lanes(champion_map,winrate_map,key,cursor,amount):
    # Grab 2000 games at random from our sql database
    statement = "SELECT * FROM games ORDER BY random() LIMIT 10000"
    output = cursor.execute(statement).fetchall()
    correct = 0

    total = 0

    for (gameId,winner,top1,jungle1,middle1,bottom1,support1,top2,jungle2,middle2,bottom2,support2) in output:
        total_1 = 0
        total_2 = 0
        times_repeated = 0
        roles_1 = ["top","jungle","middle","bottom","support"]
        roles_2 = ["top","jungle","middle","bottom","support"]
        request = requests.get("https://na1.api.riotgames.com/lol/match/v4/matches/{}?api_key={}".format(gameId,key))
        if request.status_code != 200:
            print("Sleeping for 2 minutes")
            time.sleep(120)
            continue
        game = request.json()
        #print(len(game["participants"]))
        if "participants" not in game.keys():
            continue
        # Now get the champions and keep track of them
        for participant in game["participants"]:
            used_winrate = 0
            count = 0
            champion_name = champion_map[participant["championId"]]
            lane = participant["timeline"]["lane"].lower()
            # If the player was top lane
            if lane == "TOP":
                (position,winrate) = winrate_map[champion_name]["'top'"]
                if winrate != "'None'":
                    # Remove extraneous characters that end up on the file for whatever reason
                    wr = float(winrate.replace("'",""))
                # Otherwise we need to do an average of all the winrates
                else:
                    # Make sure we're only grabbing winrates that exist
                    for (position,winrate) in winrate_map[champion_name]:
                        if winrate != "'None'":
                            # Remove extraneous characters that end up on the file for whatever reason
                            used_winrate += used_winrate + float(winrate.replace("'",""))
                            count += 1
                    wr = used_winrate / count
                if int(participant["teamId"]) == 100:
                    total_1 += wr
                    roles_1.remove("top")
                else:
                    total_2 += wr
                    roles_2.remove("top")
            elif lane == "JUNGLE":
                (position,winrate) = winrate_map[champion_name]["'jungle'"]
                if winrate != "'None'":
                    # Remove extraneous characters that end up on the file for whatever reason
                    wr = float(winrate.replace("'",""))
                # Otherwise we need to do an average of all the winrates
                else:
                    # Make sure we're only grabbing winrates that exist
                    for (position,winrate) in winrate_map[champion_name]:
                        if winrate != "'None'":
                            # Remove extraneous characters that end up on the file for whatever reason
                            used_winrate = used_winrate + float(winrate.replace("'",""))
                            count += 1
                    wr = used_winrate / count
                if int(participant["teamId"]) == 100:
                    total_1 += wr
                    roles_1.remove("jungle")
                else:
                    total_2 += wr
                    roles_2.remove("jungle")
            elif lane == "MIDDLE":
                (position,winrate) = winrate_map[champion_name]["'mid'"]
                if winrate != "'None'":
                    # Remove extraneous characters that end up on the file for whatever reason
                    wr = float(winrate.replace("'",""))
                # Otherwise we need to do an average of all the winrates
                else:
                    # Make sure we're only grabbing winrates that exist
                    for (position,winrate) in winrate_map[champion_name]:
                        if winrate != "'None'":
                            # Remove extraneous characters that end up on the file for whatever reason
                            used_winrate = used_winrate + float(winrate.replace("'",""))
                            count += 1
                    wr = used_winrate / count
                if int(participant["teamId"]) == 100:
                    total_1 += wr
                    roles_1.remove("middle")
                else:
                    total_2 += wr
                    roles_2.remove("middle")
            elif lane == "BOTTOM":
                (position,winrate) = winrate_map[champion_name]["'bot'"]
                if winrate != "'None'":
                    # Remove extraneous characters that end up on the file for whatever reason
                    wr = float(winrate.replace("'",""))
                # Otherwise we need to do an average of all the winrates
                else:
                    # Make sure we're only grabbing winrates that exist
                    for (position,winrate) in winrate_map[champion_name]:
                        if winrate != "'None'":
                            # Remove extraneous characters that end up on the file for whatever reason
                            used_winrate = used_winrate + float(winrate.replace("'",""))
                            count += 1
                    wr = used_winrate / count
                if int(participant["teamId"]) == 100:
                    total_1 += wr
                    roles_1.remove("bottom")
                else:
                    total_2 += wr
                    roles_2.remove("bottom")
            elif lane == "SUPPORT":
                (position,winrate) = winrate_map[champion_name]["'support'"]
                if winrate != "'None'":
                    # Remove extraneous characters that end up on the file for whatever reason
                    wr = float(winrate.replace("'",""))
                # Otherwise we need to do an average of all the winrates
                else:
                    # Make sure we're only grabbing winrates that exist
                    for (position,winrate) in winrate_map[champion_name]:
                        if winrate != "'None'":
                            # Remove extraneous characters that end up on the file for whatever reason
                            used_winrate = used_winrate + float(winrate.replace("'",""))
                            count += 1
                    wr = used_winrate / count
                if int(participant["teamId"]) == 100:
                    total_1 += wr
                    roles_1.remove("support")
                else:
                    total_2 += wr
                    roles_2.remove("support")
            else:
                #times_repeated += 1
                #if times_repeated > 5:
                # Make sure we're only grabbing winrates that exist
                for (position,winrate) in winrate_map[champion_name]:
                    if winrate != "'None'":
                        # Remove extraneous characters that end up on the file for whatever reason
                        used_winrate = used_winrate + float(winrate.replace("'",""))
                        count += 1
                wr = used_winrate / count
                if int(participant["teamId"]) == 100:
                    total_1 += wr
                else:
                    total_2 += wr
            #print(total_1)
            #print(total_2)
        #print(gameId)
        if total_1 > total_2:
            guessed = 1
        else:
            guessed = -1
        if guessed == winner:
            correct += 1
        total += 1

    print("%d %d %.2f" %(correct,total,100 * correct/total))
    return (correct,total)

############################
# Driver and Error Handler #
############################

def main():
    # Build winrates and champion maps based on our winrate mapping from opgg
    map = read_from_file()
    champions = read_champ_mapping()
    damage_types = read_damage_types()

    sql = sqlite3.connect("/home/vmuser/final_project_320/database.db")
    cursor = sql.cursor()

    # API Key to call the api
    key = "RGAPI-51455ed6-25ff-4da4-a08c-f480312a1d07"
    print("Starting")
    amount = {1000: [],2000: [], 3000: [],4000: []}
    for i in range(0,10):
        for am in amount.keys():
            (correct,total) = try_for_lanes(champions,map,key,cursor,am)
            amount[am].append(100.0 * correct / total)
    #show_histogram()
    # Ask if we need to update our winrate
    update = input("Should I pull new winrates? [y/n]\n")
    update = update.strip().lower()
    if update == "yes" or update == "y":
        update_wr()




    initialize_database(cursor)

    aggregate = input("Should we aggregate more data? [y/n]\n")
    if aggregate == "yes" or aggregate == "y":
        # Now we start training our data
        data_aggregator(key,champions,sql,cursor)

    times = 0
    graph_winrate_by_roles()

    f = open("data/non_trained_outcomes","w+")
    f_2 = open("data/non_trained_outcomes_2","w+")
    trained = open("data/outcomes","w")
    val = [1000,2000,3000,4000,5000,6000,7000,8000]

    out_map = {}

    #while times < 3:
    out_1 = 0
    out_2 = 0
    out_3 = 0

    statement = "SELECT * FROM games ORDER BY random() LIMIT %d" %(v,)
    output = cursor.execute(statement)
    #rand_num = random.randint(100,8000)
    values = output.fetchall()

    #(correct,total) = guess_without_training(map,champions,damage_types,sql,cursor,1.0,0.0,values)

    #out_1 += correct

    (correct,total) = guess_only_ad_ap(map,champions,damage_types,sql,cursor,0.0,1.0,values)

    out_2 += correct

    out_map[v] = 100.0 * out_2 / v

    #f.write("%d\t%d\t%.2f\n" % (correct,total,100.0 * float(correct / total)))

    #(correct,total) = guess_without_training(map,champions,damage_types,sql,cursor,0.5,0.5,values)

    #out_2 += correct

    #f_2.write("%d\t%d\t%.2f\n" % (correct,total,100.0 * float(correct / total)))

    #(correct,total) = train(map,champions,damage_types,sql,cursor,values)

    #out_3 += correct

    #trained.write("%d\t%d\t%.2f\n" % (correct,total,100.0 * float(correct / total)))
    times += 1

    print("Average correct rate was: %.1f" % (100.0 * out_2 / v,))
    #print("Average correct rate was: %.1f" % (100.0 * out_2 / rand_num,))
    #print("Average correct rate was: %.1f" % (100.0 * out_3 / rand_num,))
    plot_output_correct(out_map)

    #read_and_graph("non_trained_outcomes")

    sql.close()

def handler(signal_received, frame):
    # Handle any cleanup here
    print("printing stack")
    traceback.print_tb(frame)
    print('SIGINT or CTRL-C detected. Exiting gracefully')
    exit(0)

if __name__ == "__main__":
    # Tell Python to run the handler() function when SIGINT is recieved
    signal(SIGINT, handler)
    main()
